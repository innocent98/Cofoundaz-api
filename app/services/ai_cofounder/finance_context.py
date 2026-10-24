"""Finance grounding for the AI Co-Founder chat.

A compact, numbers-only summary of the workspace's money position, handed to the advisor so it
can talk about runway and cash with the founder's real figures instead of guessing.

Two rules shape this module:
  * Only people who may read Finance get it. The check reuses `FINANCE_ROLES` (the same set the
    Finance routes use) against the asker's ACTIVE membership of this workspace, so a mentor or
    consultant who somehow opens the chat never sees a number they could not see on the page.
  * Nothing user-authored goes in. The summary is figures only (no client names, vendors or
    descriptions), so a hostile invoice or transaction cannot inject instructions into the prompt.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import FINANCE_ROLES, InvoiceStatus, MembershipStatus
from app.db.models.invoice import Invoice
from app.db.models.membership import Membership
from app.services.finance.cashflow import cash_flow_summary


def can_read_finance(db: Session, *, startup_id: uuid.UUID, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    role = (
        db.query(Membership.role)
        .filter(
            Membership.startup_id == startup_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active,
        )
        .scalar()
    )
    return role in FINANCE_ROLES


def finance_summary(
    db: Session, *, startup_id: uuid.UUID, today: dt.date | None = None
) -> dict[str, Any]:
    """Cash on hand, burn, revenue (Funding excluded, D-FIN-11), runway and overdue invoices.
    Money is in minor units (kobo), exactly as the Finance API reports it."""
    as_of = today or dt.datetime.now(dt.UTC).date()
    cash = cash_flow_summary(db, startup_id=startup_id)
    overdue = (
        db.query(Invoice.total_minor)
        .filter(
            Invoice.startup_id == startup_id,
            Invoice.status == InvoiceStatus.sent,
            Invoice.due_on < as_of,
        )
        .all()
    )
    return {
        "currency": cash["currency"],
        "cash_on_hand": cash["cash_on_hand"],
        "monthly_burn": cash["monthly_burn"],
        "monthly_revenue": cash["monthly_revenue"],
        "runway_months": cash["runway_months"],
        "overdue_invoice_count": len(overdue),
        "overdue_invoice_total": sum(int(t) for (t,) in overdue),
    }


def _money(minor: int, currency: str) -> str:
    return f"{currency} {minor / 100:,.0f}"


def finance_context_for(
    db: Session, *, startup_id: uuid.UUID, user_id: uuid.UUID | None
) -> str | None:
    """The prompt paragraph, or None when the asker may not read Finance."""
    if not can_read_finance(db, startup_id=startup_id, user_id=user_id):
        return None
    s = finance_summary(db, startup_id=startup_id)
    cur = s["currency"]
    runway = (
        f"{s['runway_months']} months"
        if s["runway_months"] is not None
        else "not burning cash, or out of cash (no runway figure)"
    )
    return (
        "Finance snapshot (from their ledger, use it when relevant, never invent other figures): "
        f"cash on hand {_money(s['cash_on_hand'], cur)}; monthly burn {_money(s['monthly_burn'], cur)}; "
        f"monthly revenue {_money(s['monthly_revenue'], cur)} (3-month average, funding rounds "
        f"excluded); runway {runway}; overdue invoices {s['overdue_invoice_count']} "
        f"totalling {_money(s['overdue_invoice_total'], cur)}."
    )
