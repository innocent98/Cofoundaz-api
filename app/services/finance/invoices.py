import datetime as dt
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import InvoiceStatus, InvoiceTerms
from app.db.models.invoice import Invoice
from app.schemas.invoice import InvoiceCreate, InvoiceResponse, MoneyLine
from app.services.finance.errors import _validation

_INT32 = 2_147_483_647
_NUMBER_RETRIES = 5


def _compute_totals(line_items: list[dict[str, Any]], tax_percent: float) -> tuple[int, int, int]:
    subtotal = sum(int(li["quantity"]) * int(li["unit_price_minor"]) for li in line_items)
    if subtotal > _INT32:
        raise _validation("line_items", "Invoice subtotal exceeds the maximum allowed amount.")
    # Decimal + ROUND_HALF_UP (not float round(), which is banker's rounding): a half-kobo of tax
    # rounds up, as expected for money.
    tax = int(
        (Decimal(subtotal) * Decimal(str(tax_percent)) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    total = subtotal + tax
    if total > _INT32:
        raise _validation("line_items", "Invoice total exceeds the maximum allowed amount.")
    return subtotal, tax, total


def _next_number(db: Session, *, startup_id: uuid.UUID, year: int) -> str:
    prefix = f"INV-{year}-"
    existing = (
        db.query(Invoice.number)
        .filter(Invoice.startup_id == startup_id, Invoice.number.like(f"{prefix}%"))
        .all()
    )
    # max suffix + 1 (not count + 1): stays monotonic even if a row is ever removed.
    highest = 0
    for (number,) in existing:
        suffix = number[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:03d}"


def _derived_status(inv: Invoice, *, today: dt.date) -> str:
    if inv.status == InvoiceStatus.sent and inv.due_on is not None and inv.due_on < today:
        return "overdue"
    return inv.status.value


def create_invoice(db: Session, *, startup_id: uuid.UUID, data: InvoiceCreate) -> Invoice:
    line_items = [li.model_dump() for li in data.line_items]
    # tax_percent is stored as NUMERIC(5,2); quantise first so the stored rate is the applied one.
    tax_percent = Decimal(str(data.tax_percent)).quantize(Decimal("0.01"))
    subtotal, tax, total = _compute_totals(line_items, float(tax_percent))
    year = dt.datetime.now(dt.UTC).year

    for attempt in range(_NUMBER_RETRIES):
        number = _next_number(db, startup_id=startup_id, year=year)
        # add() must be inside the SAVEPOINT so a failed insert is rolled back with it and does
        # not poison the outer session.
        try:
            with db.begin_nested():
                inv = Invoice(
                    startup_id=startup_id,
                    number=number,
                    client_name=data.client_name,
                    client_email=str(data.client_email),
                    line_items=line_items,
                    subtotal_minor=subtotal,
                    tax_percent=tax_percent,
                    tax_minor=tax,
                    total_minor=total,
                    currency=data.currency,
                    terms=InvoiceTerms(data.terms),
                    status=InvoiceStatus.draft,
                )
                db.add(inv)
        except IntegrityError:
            if attempt == _NUMBER_RETRIES - 1:
                raise
            continue
        db.flush()
        return inv
    raise AssertionError("unreachable")  # pragma: no cover


def get_invoice(db: Session, *, startup_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    inv = db.query(Invoice).filter_by(id=invoice_id, startup_id=startup_id).one_or_none()
    if inv is None:
        raise NotFound()
    return inv


def list_invoices(
    db: Session, *, startup_id: uuid.UUID, status: str | None = None, today: dt.date | None = None
) -> list[Invoice]:
    rows = (
        db.query(Invoice)
        .filter_by(startup_id=startup_id)
        .order_by(Invoice.created_at.desc(), Invoice.number.desc())
        .all()
    )
    if status is None:
        return rows
    as_of = today or dt.datetime.now(dt.UTC).date()
    return [r for r in rows if _derived_status(r, today=as_of) == status]


def serialize_invoice(inv: Invoice, *, today: dt.date) -> InvoiceResponse:
    lines = [
        MoneyLine(
            description=li["description"],
            quantity=li["quantity"],
            unit_price_minor=li["unit_price_minor"],
            amount_minor=int(li["quantity"]) * int(li["unit_price_minor"]),
        )
        for li in inv.line_items
    ]
    return InvoiceResponse(
        id=inv.id,
        number=inv.number,
        client_name=inv.client_name,
        client_email=inv.client_email,
        line_items=lines,
        subtotal_minor=inv.subtotal_minor,
        tax_percent=float(inv.tax_percent),
        tax_minor=inv.tax_minor,
        total_minor=inv.total_minor,
        currency=inv.currency,
        terms=inv.terms.value,
        status=_derived_status(inv, today=today),
        issued_on=inv.issued_on,
        due_on=inv.due_on,
        paid_at=inv.paid_at,
        auto_remind=inv.auto_remind,
        transaction_id=inv.transaction_id,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )
