import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.schemas.finance import TransactionCreate, TransactionResponse, TransactionUpdate
from app.services.finance.errors import _validation


def serialize_transaction(row: Transaction) -> TransactionResponse:
    return TransactionResponse.model_validate(row, from_attributes=True)


def create_transaction(
    db: Session, *, startup_id: uuid.UUID, data: TransactionCreate
) -> Transaction:
    # source is forced to manual: TransactionCreate has no source field, so a client cannot set it.
    row = Transaction(startup_id=startup_id, source=TransactionSource.manual, **data.model_dump())
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


def list_transactions(
    db: Session,
    *,
    startup_id: uuid.UUID,
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Transaction]:
    q = db.query(Transaction).filter_by(startup_id=startup_id)
    if uncategorized:
        q = q.filter(Transaction.category.is_(None))
    elif category is not None:
        q = q.filter(Transaction.category == category)
    if direction is not None:
        q = q.filter(Transaction.direction == direction)
    if date_from is not None:
        q = q.filter(Transaction.date >= date_from)
    if date_to is not None:
        q = q.filter(Transaction.date <= date_to)
    return q.order_by(Transaction.date.desc(), Transaction.created_at.desc()).all()


def _reject_invoice_managed(row: Transaction) -> None:
    # The inflow created by invoice mark-paid is owned by the invoice; editing or deleting it
    # directly would leave the invoice claiming a ledger entry that no longer matches.
    if row.source == TransactionSource.invoice:
        raise _validation(
            "source",
            "This transaction is managed by an invoice; unpay the invoice to change or remove it.",
        )


def update_transaction(
    db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction:
    row = get_transaction(db, startup_id=startup_id, transaction_id=transaction_id)
    _reject_invoice_managed(row)
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    db.flush()
    return row


def delete_transaction(db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID) -> None:
    row = get_transaction(db, startup_id=startup_id, transaction_id=transaction_id)
    _reject_invoice_managed(row)
    db.delete(row)
    db.flush()
