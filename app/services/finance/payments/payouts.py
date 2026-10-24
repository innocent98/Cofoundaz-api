"""D-FIN-10: spot a Paystack settlement among the bank's credits.

When a workspace has Paystack connected, the ledger already holds each Paystack charge as money
in. The payout that later lands in the bank is the same money moving to the founder's own
account, so counting it again would double revenue and cash. A bank credit is therefore filed
under the "Paystack payout" category, which the totals leave out (cashflow._base_query), only
when BOTH hold: its narration names Paystack, and the ledger's Paystack charges of the days
before it add up to at least its amount (`charges_cover`). A Paystack-narrated credit that the
charges cannot explain stays ordinary income. Still not a settlement-level reconciliation, so the
founder can re-categorise either way: category is the only thing editable on an imported row.
"""

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.finance_connection import FinanceConnection

_NARRATION = re.compile(r"\bpaystack\b|\bpstk\b", re.IGNORECASE)


def narration_names_paystack(narration: str) -> bool:
    return _NARRATION.search(narration) is not None


PAYOUT_WINDOW_DAYS = 10


def charges_cover(db: Session, *, startup_id: uuid.UUID, on: date, amount_minor: int) -> bool:
    """True when the ledger's Paystack charges of the PAYOUT_WINDOW_DAYS up to `on` add up to at
    least `amount_minor`. A payout can only be money already counted as charges, so a Paystack-
    narrated credit the charges cannot explain (a client paying by Paystack Transfer) stays income.
    """
    total = db.execute(
        select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
            Transaction.startup_id == startup_id,
            Transaction.source == TransactionSource.paystack,
            Transaction.direction == TransactionDirection.inflow,
            Transaction.date >= on - timedelta(days=PAYOUT_WINDOW_DAYS),
            Transaction.date <= on,
        )
    ).scalar_one()
    return int(total) >= amount_minor


def has_paystack(db: Session, *, startup_id: uuid.UUID) -> bool:
    """True once the workspace has ever connected Paystack: disconnecting keeps its history, and
    payouts for that history keep arriving."""
    return (
        db.execute(
            select(FinanceConnection.id)
            .where(
                FinanceConnection.startup_id == startup_id,
                FinanceConnection.provider == "paystack",
            )
            .limit(1)
        ).first()
        is not None
    )
