import uuid
from datetime import date, datetime

from sqlalchemy import func, tuple_
from sqlalchemy.orm import Query, Session

from app.core.errors import NotFound
from app.core.pagination import DEFAULT_LIMIT, decode_keyset, encode_keyset
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.schemas.finance import TransactionCreate, TransactionResponse, TransactionUpdate
from app.services.finance.categories import normalize_category
from app.services.finance.errors import _validation


def serialize_transaction(row: Transaction) -> TransactionResponse:
    return TransactionResponse.model_validate(row, from_attributes=True)


def create_transaction(
    db: Session, *, startup_id: uuid.UUID, data: TransactionCreate
) -> Transaction:
    # source is forced to manual: TransactionCreate has no source field, so a client cannot set it.
    fields = data.model_dump()
    fields["category"] = normalize_category(db, startup_id=startup_id, raw=fields["category"])
    row = Transaction(startup_id=startup_id, source=TransactionSource.manual, **fields)
    db.add(row)
    db.flush()
    return row


def get_transaction(
    db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID
) -> Transaction:
    row = db.query(Transaction).filter_by(id=transaction_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def filtered_query(
    db: Session,
    *,
    startup_id: uuid.UUID,
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Query[Transaction]:
    """The ledger filters shared by the list endpoint and the exports."""
    q = db.query(Transaction).filter_by(startup_id=startup_id)
    if uncategorized:
        q = q.filter(Transaction.category.is_(None))
    elif category is not None:
        q = q.filter(func.lower(Transaction.category) == category.strip().casefold())
    if direction is not None:
        q = q.filter(Transaction.direction == direction)
    if date_from is not None:
        q = q.filter(Transaction.date >= date_from)
    if date_to is not None:
        q = q.filter(Transaction.date <= date_to)
    return q


def ordered(q: Query[Transaction]) -> Query[Transaction]:
    return q.order_by(Transaction.date.desc(), Transaction.created_at.desc(), Transaction.id.desc())


def list_transactions(
    db: Session,
    *,
    startup_id: uuid.UUID,
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[list[Transaction], str | None, int]:
    """One page, newest first: (rows, next_cursor, total matching the filters)."""
    q = filtered_query(
        db,
        startup_id=startup_id,
        category=category,
        uncategorized=uncategorized,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
    )
    total = q.count()
    if cursor:
        after = decode_keyset(cursor, date.fromisoformat, datetime.fromisoformat, uuid.UUID)
        q = q.filter(
            tuple_(Transaction.date, Transaction.created_at, Transaction.id) < tuple_(*after)
        )
    rows = ordered(q).limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_keyset(
            last.date.isoformat(), last.created_at.isoformat(), str(last.id)
        )
    return rows, next_cursor, total


# Every source that mirrors an outside system (Mono bank feed, payment providers, accounting
# import) is category-only; only `manual` rows are fully editable. Disconnecting an integration
# keeps its history, so no public route needs to delete an imported row.
_IMPORTED_SOURCES = frozenset(
    {
        TransactionSource.bank,
        TransactionSource.accounting,
        TransactionSource.paystack,
        TransactionSource.flutterwave,
    }
)


def _reject_managed(row: Transaction, *, changed: set[str] | None = None) -> None:
    """`changed` is the set of fields a PATCH sets; None means a DELETE."""
    # Inflows created by invoice mark-paid and outflows created by expenses are owned by their
    # parent record; editing or deleting them directly would leave the parent claiming a ledger
    # entry that no longer matches.
    if row.source == TransactionSource.invoice:
        raise _validation(
            "source",
            "This transaction is managed by an invoice; unpay the invoice to change or remove it.",
        )
    if row.source == TransactionSource.expense:
        raise _validation(
            "source",
            "This transaction is managed by an expense; edit or delete the expense instead.",
        )
    if row.source in _IMPORTED_SOURCES and (changed is None or changed - {"category"}):
        raise _validation("source", "Imported transactions are read-only except the category.")


def update_transaction(
    db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction:
    row = get_transaction(db, startup_id=startup_id, transaction_id=transaction_id)
    fields = data.model_dump(exclude_unset=True)
    _reject_managed(row, changed=set(fields))
    if "category" in fields:
        fields["category"] = normalize_category(db, startup_id=startup_id, raw=fields["category"])
    for name, value in fields.items():
        setattr(row, name, value)
    db.flush()
    return row


def delete_transaction(db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID) -> None:
    row = get_transaction(db, startup_id=startup_id, transaction_id=transaction_id)
    _reject_managed(row)
    db.delete(row)
    db.flush()
