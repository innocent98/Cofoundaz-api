"""D-FIN-10: spot a Paystack settlement among the bank's credits.

When a workspace has Paystack connected, the ledger already holds each Paystack charge as money
in. The payout that later lands in the bank is the same money moving to the founder's own
account, so counting it again would double revenue and cash. A bank credit whose narration
names Paystack is therefore filed under the "Paystack payout" category, which the totals leave
out (cashflow._base_query). It is a narration match, not a settlement-level reconciliation, so
the founder can re-categorise a false positive: the category is the only thing they can edit on
an imported row.
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.finance_connection import FinanceConnection

_NARRATION = re.compile(r"\bpaystack\b|\bpstk\b", re.IGNORECASE)


def narration_names_paystack(narration: str) -> bool:
    return _NARRATION.search(narration) is not None


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
