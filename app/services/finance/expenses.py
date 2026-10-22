import calendar
import datetime as dt
import re
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func, tuple_
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.pagination import DEFAULT_LIMIT, decode_keyset, encode_keyset
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.expense import Expense
from app.db.models.finance import Transaction
from app.platform.signed_urls import sign_key
from app.platform.storage import get_storage
from app.schemas.expense import ExpenseCreate, ExpenseResponse, ExpenseUpdate
from app.services.finance.categories import normalize_category
from app.services.finance.errors import _validation

# The only route a client ever gets for a receipt; it returns a short-lived signed link.
RECEIPT_ROUTE = "/api/v1/finance/expenses/{expense_id}/receipt"
RECEIPT_LINK_TTL_SECONDS = 300
# Where our own signed links point (local storage); Cloudinary links come from Cloudinary.
LOCAL_DOWNLOAD_PATH = "/api/v1/finance/receipts/download"


def _month_range(month: str) -> tuple[dt.date, dt.date]:
    """Inclusive first/last day of a "YYYY-MM" calendar month."""
    try:
        year_s, month_s = month.split("-")
        first = dt.date(int(year_s), int(month_s), 1)
    except ValueError:
        raise _validation("month", "month must be in YYYY-MM format.") from None
    last = dt.date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    return first, last


def _post_outflow(db: Session, exp: Expense) -> Transaction:
    # 1:1 with the expense: this row is what makes the spend reduce cash-on-hand / raise burn.
    txn = Transaction(
        startup_id=exp.startup_id,
        date=exp.expense_date,
        description=f"Expense: {exp.vendor}",
        category=exp.category,
        amount_minor=exp.amount_minor,
        currency=exp.currency,
        direction=TransactionDirection.outflow,
        source=TransactionSource.expense,
    )
    db.add(txn)
    db.flush()
    return txn


def create_expense(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: ExpenseCreate
) -> Expense:
    fields = data.model_dump()
    fields["category"] = normalize_category(db, startup_id=startup_id, raw=fields["category"])
    exp = Expense(startup_id=startup_id, created_by=created_by, **fields)
    db.add(exp)
    db.flush()
    txn = _post_outflow(db, exp)
    exp.transaction_id = txn.id
    db.flush()
    return exp


def get_expense(db: Session, *, startup_id: uuid.UUID, expense_id: uuid.UUID) -> Expense:
    exp = db.query(Expense).filter_by(id=expense_id, startup_id=startup_id).one_or_none()
    if exp is None:
        raise NotFound()
    return exp


# Fields mirrored onto the linked outflow so the ledger keeps reconciling with the expense.
_LEDGER_FIELDS = frozenset({"amount_minor", "expense_date", "category", "vendor", "currency"})


def update_expense(
    db: Session, *, startup_id: uuid.UUID, expense_id: uuid.UUID, data: ExpenseUpdate
) -> Expense:
    exp = get_expense(db, startup_id=startup_id, expense_id=expense_id)
    fields = data.model_dump(exclude_unset=True)
    if "category" in fields:
        fields["category"] = normalize_category(db, startup_id=startup_id, raw=fields["category"])
    for name, value in fields.items():
        setattr(exp, name, value)
    if _LEDGER_FIELDS & fields.keys() and exp.transaction_id is not None:
        # Same transaction row is updated in place so `transaction_id` stays stable.
        txn = db.get(Transaction, exp.transaction_id)
        if txn is not None:
            txn.amount_minor = exp.amount_minor
            txn.date = exp.expense_date
            txn.category = exp.category
            txn.currency = exp.currency
            txn.description = f"Expense: {exp.vendor}"
    db.flush()
    return exp


def delete_expense(db: Session, *, startup_id: uuid.UUID, expense_id: uuid.UUID) -> None:
    exp = get_expense(db, startup_id=startup_id, expense_id=expense_id)
    if exp.transaction_id is not None:
        txn = db.get(Transaction, exp.transaction_id)
        if txn is not None:
            db.delete(txn)
    if exp.receipt_key:
        _delete_asset(exp.receipt_key, exp.receipt_private)
    db.delete(exp)
    db.flush()


MAX_RECEIPT_BYTES = 10 * 1024 * 1024

# The exact shape of every key `attach_receipt` mints. The download route refuses anything else
# before it reaches the filesystem, whatever a (validly signed) token claims.
RECEIPT_KEY_PATTERN = re.compile(r"receipts/[0-9a-f-]{36}/[0-9a-f]{32}\.(?:png|jpg|pdf)")

# Receipts are classified by their leading bytes, never by the client's Content-Type or filename:
# an HTML file labelled image/png must not be stored (or later served) as an image.
_RECEIPT_TYPES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"%PDF-", "application/pdf", ".pdf"),
)
_EXT_TO_TYPE = {ext: ctype for _magic, ctype, ext in _RECEIPT_TYPES}


def sniff_receipt(content: bytes) -> tuple[str, str] | None:
    """(content_type, extension) for a PNG, JPEG or PDF by magic bytes, else None."""
    for magic, ctype, ext in _RECEIPT_TYPES:
        if content.startswith(magic):
            return ctype, ext
    return None


def receipt_content_type(key: str) -> str:
    """Content type for a stored receipt key (the extension was chosen from the sniffed bytes)."""
    return _EXT_TO_TYPE.get("." + key.rsplit(".", 1)[-1], "application/octet-stream")


def attach_receipt(
    db: Session,
    *,
    startup_id: uuid.UUID,
    expense_id: uuid.UUID,
    filename: str | None,
    content_type: str | None,
    size_bytes: int,
    content: bytes,
) -> Expense:
    """`filename` and `content_type` are client-supplied and deliberately ignored."""
    exp = get_expense(db, startup_id=startup_id, expense_id=expense_id)
    if size_bytes > MAX_RECEIPT_BYTES:
        raise _validation("receipt", "Receipt exceeds the 10 MB limit.")
    sniffed = sniff_receipt(content)
    if sniffed is None:
        raise _validation("receipt", "Receipt must be a PNG, JPEG, or PDF.")
    ctype, ext = sniffed
    old = (exp.receipt_key, exp.receipt_private) if exp.receipt_key else None
    key = f"receipts/{startup_id}/{uuid.uuid4().hex}{ext}"
    # Save the new asset before dropping the old one so a failed upload never orphans the row.
    get_storage().save_private(key, content, ctype)
    exp.receipt_key, exp.receipt_private, exp.receipt_url = key, True, None
    if old is not None:
        _delete_asset(*old)
    db.flush()
    return exp


def _delete_asset(key: str, private: bool) -> None:
    storage = get_storage()
    (storage.delete_private if private else storage.delete)(key)


def remove_receipt(db: Session, *, startup_id: uuid.UUID, expense_id: uuid.UUID) -> Expense:
    exp = get_expense(db, startup_id=startup_id, expense_id=expense_id)
    if exp.receipt_key:
        _delete_asset(exp.receipt_key, exp.receipt_private)
    exp.receipt_url = exp.receipt_key = None
    exp.receipt_private = False
    db.flush()
    return exp


def receipt_link(
    db: Session, *, startup_id: uuid.UUID, expense_id: uuid.UUID, api_public_url: str
) -> dict[str, Any]:
    """A short-lived signed link to the expense's receipt (workspace-scoped, 404 otherwise).

    Legacy rows (uploaded before receipts were private) are moved behind authentication the first
    time they are opened; if that move fails the link is refused rather than handing out a public
    URL.
    """
    exp = get_expense(db, startup_id=startup_id, expense_id=expense_id)
    if not exp.receipt_key:
        raise NotFound()
    storage = get_storage()
    if not exp.receipt_private:
        storage.make_private(exp.receipt_key)
        exp.receipt_private = True
        exp.receipt_url = None
        db.flush()
    url = storage.private_url(exp.receipt_key, RECEIPT_LINK_TTL_SECONDS)
    if url is None:
        token, expires = sign_key(exp.receipt_key, ttl_seconds=RECEIPT_LINK_TTL_SECONDS)
        url = f"{api_public_url.rstrip('/')}{LOCAL_DOWNLOAD_PATH}?token={token}"
    else:
        expires = int(dt.datetime.now(dt.UTC).timestamp()) + RECEIPT_LINK_TTL_SECONDS
    return {
        "url": url,
        "expires_at": dt.datetime.fromtimestamp(expires, dt.UTC),
        "content_type": receipt_content_type(exp.receipt_key),
    }


def list_expenses(
    db: Session,
    *,
    startup_id: uuid.UUID,
    category: str | None = None,
    month: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    recurring: bool | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[list[Expense], str | None, int]:
    """One page, newest first: (rows, next_cursor, total matching the filters)."""
    q = db.query(Expense).filter_by(startup_id=startup_id)
    if category is not None:
        q = q.filter(func.lower(Expense.category) == category.strip().casefold())
    if month is not None:
        first, last = _month_range(month)
        q = q.filter(Expense.expense_date >= first, Expense.expense_date <= last)
    if date_from is not None:
        q = q.filter(Expense.expense_date >= date_from)
    if date_to is not None:
        q = q.filter(Expense.expense_date <= date_to)
    if recurring is not None:
        q = q.filter(Expense.recurring == recurring)
    total = q.count()
    if cursor:
        after = decode_keyset(cursor, dt.date.fromisoformat, dt.datetime.fromisoformat, uuid.UUID)
        q = q.filter(tuple_(Expense.expense_date, Expense.created_at, Expense.id) < tuple_(*after))
    rows = (
        q.order_by(Expense.expense_date.desc(), Expense.created_at.desc(), Expense.id.desc())
        .limit(limit + 1)
        .all()
    )
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last_row = rows[-1]
        next_cursor = encode_keyset(
            last_row.expense_date.isoformat(), last_row.created_at.isoformat(), str(last_row.id)
        )
    return rows, next_cursor, total


def serialize_expense(exp: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=exp.id,
        vendor=exp.vendor,
        category=exp.category,
        expense_date=exp.expense_date,
        amount_minor=exp.amount_minor,
        currency=exp.currency,
        recurring=exp.recurring,
        notes=exp.notes,
        has_receipt=exp.receipt_key is not None,
        receipt_url=RECEIPT_ROUTE.format(expense_id=exp.id) if exp.receipt_key else None,
        transaction_id=exp.transaction_id,
        created_by=exp.created_by,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
    )


def expense_category_totals(db: Session, *, startup_id: uuid.UUID, month: str) -> dict[str, int]:
    """category -> summed amount_minor of that category's expenses in `month` ("YYYY-MM").

    Backs the Expenses page summary (expenses only). Budget actuals use the ledger instead
    (`budgets.ledger_outflow_totals`, D-FIN-3).
    """
    first, last = _month_range(month)
    rows = (
        db.query(Expense.category, func.sum(Expense.amount_minor))
        .filter(
            Expense.startup_id == startup_id,
            Expense.expense_date >= first,
            Expense.expense_date <= last,
        )
        .group_by(Expense.category)
        .all()
    )
    return {cat: int(total) for cat, total in rows}


def category_summary(db: Session, *, startup_id: uuid.UUID, month: str) -> dict[str, Any]:
    first, last = _month_range(month)
    totals = expense_category_totals(db, startup_id=startup_id, month=month)
    grand_total = sum(totals.values())
    currencies = Counter(
        c
        for (c,) in db.query(Expense.currency)
        .filter(
            Expense.startup_id == startup_id,
            Expense.expense_date >= first,
            Expense.expense_date <= last,
        )
        .all()
    )
    currency = currencies.most_common(1)[0][0] if currencies else "NGN"

    rows: list[dict[str, Any]] = []
    if grand_total > 0:  # guard: an all-zero month has no meaningful percentages
        rows = [
            {
                "category": cat,
                "total_minor": total,
                "percent": round(100 * total / grand_total, 1),
            }
            for cat, total in sorted(totals.items(), key=lambda r: (-r[1], r[0]))
        ]
    return {"month": month, "currency": currency, "total_minor": grand_total, "rows": rows}
