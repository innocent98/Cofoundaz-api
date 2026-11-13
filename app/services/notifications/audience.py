"""Who a notification type is for: the single source for fan-out AND the read filter.

`registry.SPECS` builds each restricted type's recipients from `AUDIENCE`, and the read paths in
`service.py` hide a row from any role outside it. Rows stored before an audience rule landed
(#150/#152/#153) are therefore hidden on read too, without touching the stored data.

A type with no rule is open: it was delivered under the open default, so it stays visible.
"""

from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_, true

from app.db.models.enums import (
    BRIEFING_ROLES,
    DOCUMENT_ROLES,
    FINANCE_ROLES,
    FUNDING_ROLES,
    LEGAL_ROLES,
    MARKETING_ROLES,
    MISSION_ROLES,
    SALES_ROLES,
    TERM_SHEET_ROLES,
    MembershipRole,
)
from app.db.models.notification import Notification

CORE_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member}
)
FOUNDER_ROLES: frozenset[MembershipRole] = frozenset({MembershipRole.founder})

AUDIENCE: dict[str, frozenset[MembershipRole]] = {
    "billing.payment.failed": FOUNDER_ROLES,
    "billing.usage.threshold": FOUNDER_ROLES,
    "billing.plan.changed": FOUNDER_ROLES,
    "readiness.qa.scored": FOUNDER_ROLES,  # the scorecard quotes the founder's answers
    "readiness.improved": FOUNDER_ROLES,  # D-IR-4: the score is the founder's to act on
    "mission.ready": MISSION_ROLES,
    "mission.completed": MISSION_ROLES,
    "mission.streak.milestone": MISSION_ROLES,
    "dashboard.briefing.ready": BRIEFING_ROLES,
    "assessment.completed": CORE_ROLES,  # D-ASM-1
    "learning.course.completed": CORE_ROLES,  # the Academy is F + TM (PRD 17)
    "learning.course.recommended": CORE_ROLES,
    "ai.suggestion.created": CORE_ROLES,  # F + TM, the same people who can open the AI Co-Founder
    # D-DOC-1: Documents is F + TM until grants land; the read filter hides rows stored before.
    "document.shared": DOCUMENT_ROLES,
    "document.signature.requested": DOCUMENT_ROLES,
    "document.signature.signed": DOCUMENT_ROLES,
    "document.signature.completed": DOCUMENT_ROLES,
    "assessment.quarterly.due": FOUNDER_ROLES,
    "business.suggestion.created": FOUNDER_ROLES,  # PRD 08: the founder approves
    "validation.smoke_test.milestone": CORE_ROLES,
    "validation.insight.detected": CORE_ROLES,
    "finance.runway.low": FINANCE_ROLES,
    "finance.runway.updated": FINANCE_ROLES,
    "finance.invoice.overdue": FINANCE_ROLES,
    "finance.invoice.paid": FINANCE_ROLES,
    "marketing.post.published": MARKETING_ROLES,
    "marketing.campaign.launched": MARKETING_ROLES,
    "marketing.campaign.readout.ready": MARKETING_ROLES,
    "marketing.campaign.completed": MARKETING_ROLES,
    "funding.round.committed_changed": FUNDING_ROLES,  # D-FUND-L6: F + TM, never all members
    "funding.term_sheet.added": TERM_SHEET_ROLES,  # D-FUND-5: offers are founder only
    "funding.dataroom.viewed": FUNDING_ROLES,  # D-FUND-L6: one per share per day, F + TM
    "sales.deal.won": SALES_ROLES,
    "sales.deal.stalled": SALES_ROLES,
    "sales.sending.paused": FOUNDER_ROLES,
    "journal.reminder": FOUNDER_ROLES,  # the journal is founder-only (PRD 21)
    "journal.prompt.daily": FOUNDER_ROLES,
    # The titles name the filing, and the four excluded roles are 403 on every legal route,
    # so delivering these to them would leak exactly what the routes withhold (spec D4).
    "legal.compliance.due_soon": LEGAL_ROLES,
    "legal.compliance.overdue": LEGAL_ROLES,
    "legal.ip.renewal_due_soon": LEGAL_ROLES,
}

# The audience of this type depends on its payload: payload["audience"] == "finance" opens it to
# FINANCE_ROLES, anything else to MARKETING_ROLES (the dashboard KPI tiles' own gates).
KPI_TYPE = "kpi.threshold.crossed"


def kpi_roles(data: dict[str, Any]) -> frozenset[MembershipRole]:
    return FINANCE_ROLES if data.get("audience") == "finance" else MARKETING_ROLES


def audience_roles(type_: str, data: dict[str, Any]) -> frozenset[MembershipRole] | None:
    """The roles `type_` is delivered to, or None when the type is open to every role."""
    if type_ == KPI_TYPE:
        return kpi_roles(data)
    return AUDIENCE.get(type_)


def audience_visible(role: MembershipRole, type_: str, data: dict[str, Any]) -> bool:
    """Python twin of `audience_filter`, for single rows."""
    roles = audience_roles(type_, data)
    return roles is None or role in roles


def audience_filter(role: MembershipRole) -> ColumnElement[bool]:
    """SQL predicate for the notifications `role` may read. Applied in the query (not after it)
    so LIMIT, cursors and counts only ever see visible rows."""
    hidden = sorted(t for t, roles in AUDIENCE.items() if role not in roles)
    clauses: list[ColumnElement[bool]] = []
    if hidden:
        clauses.append(Notification.type.notin_(hidden))
    clauses.append(
        or_(
            Notification.type != KPI_TYPE,
            _kpi_ok(role),
        )
    )
    return and_(*clauses) if clauses else true()


def _kpi_ok(role: MembershipRole) -> ColumnElement[bool]:
    audience: ColumnElement[str] = Notification.data["audience"].astext
    if role in FINANCE_ROLES and role in MARKETING_ROLES:
        return true()
    if role in FINANCE_ROLES:
        return audience == "finance"
    if role in MARKETING_ROLES:
        return audience.is_distinct_from("finance")
    return false()
