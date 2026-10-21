from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import InvoiceStatus, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.finance_connection import FinanceConnection
from app.db.models.invoice import Invoice
from app.services.finance import invoices as invoices_svc
from app.services.finance.payments.base import ProviderTransaction

INT32_MAX = 2_147_483_647
CURRENCY = "NGN"

_KIND_SHAPE = {
    "charge": (TransactionDirection.inflow, "Revenue"),
    "fee": (TransactionDirection.outflow, "Payment fees"),
    "refund": (TransactionDirection.outflow, "Refunds"),
}


@dataclass
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    invoice_matched: int = 0
    skipped_currency: int = 0
    skipped_oversize: int = 0
    fees: int = 0
    refunds: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    @property
    def changed(self) -> bool:
        return bool(self.imported or self.invoice_matched or self.fees or self.refunds)


def _matching_invoice(db: Session, *, startup_id: object, reference: str) -> Invoice | None:
    return db.execute(
        select(Invoice).where(Invoice.startup_id == startup_id, Invoice.pay_reference == reference)
    ).scalar_one_or_none()


def import_items(
    db: Session, *, connection: FinanceConnection, items: Sequence[ProviderTransaction]
) -> ImportResult:
    """Idempotently turn provider rows into ledger rows.

    Provider listings return the whole page, so currency and amount filtering happens here.
    Safe under autoflush=False: every insert flushes itself inside its own SAVEPOINT.
    """
    result = ImportResult()
    source = TransactionSource(connection.provider)
    for item in items:
        if item.currency != CURRENCY:
            result.skipped_currency += 1
            continue
        if item.amount_minor <= 0 or item.amount_minor > INT32_MAX:
            result.skipped_oversize += 1
            continue

        if item.kind == "charge":
            invoice = _matching_invoice(
                db, startup_id=connection.startup_id, reference=item.reference
            )
            if invoice is not None and invoice.status == InvoiceStatus.paid:
                result.duplicates += 1
                continue
            # An underpayment (or odd currency) must not settle the invoice: record the money as a
            # plain inflow and leave the invoice `sent`, same rule as the webhook path.
            if (
                invoice is not None
                and invoice.total_minor == item.amount_minor
                and invoice.currency == CURRENCY
            ):
                paid = invoices_svc.mark_paid_via_provider(
                    db,
                    startup_id=connection.startup_id,
                    invoice_id=invoice.id,
                    provider=connection.provider,
                    now=item.occurred_at,
                )
                if paid is not None:
                    result.invoice_matched += 1
                    continue
                # Not payable (a draft): fall through and record the money as a normal inflow.

        direction, category = _KIND_SHAPE[item.kind]
        try:
            # add() INSIDE the savepoint so a unique violation rolls the pending row back with it.
            with db.begin_nested():
                db.add(
                    Transaction(
                        startup_id=connection.startup_id,
                        date=item.occurred_at.astimezone(UTC).date(),
                        description=item.description[:300],
                        category=category,
                        amount_minor=item.amount_minor,
                        currency=CURRENCY,
                        direction=direction,
                        source=source,
                        external_ref=item.reference,
                        connection_id=connection.id,
                    )
                )
        except IntegrityError:
            result.duplicates += 1
            continue
        if item.kind == "charge":
            result.imported += 1
        elif item.kind == "fee":
            result.fees += 1
        else:
            result.refunds += 1
    return result
