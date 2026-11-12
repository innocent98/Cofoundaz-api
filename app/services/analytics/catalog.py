"""The metric catalog: code, not a table (D-ANA-L2). One entry per metric, tagged with its hub.

Authorisation is per metric (D-ANA-4): a viewer receives a metric only when their role could open
the metric's hub. The numbers of a locked metric are never computed or sent.
"""

from dataclasses import dataclass, field

from app.db.models.enums import (
    FINANCE_ROLES,
    FUNDING_ROLES,
    MARKETING_ROLES,
    MISSION_ROLES,
    SALES_ROLES,
    MembershipRole,
)

# Roles that may open the Analytics Overview at all (slice B adds mentor / business consultant for
# reports the Founder shares with them; they never get the live catalog).
ANALYTICS_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant}
)
_FT = frozenset({MembershipRole.founder, MembershipRole.team_member})
CUSTOM_WRITE_ROLES = _FT

HUB_ROLES: dict[str, frozenset[MembershipRole]] = {
    "finance": FINANCE_ROLES,
    "sales": SALES_ROLES,
    "marketing": MARKETING_ROLES,
    "validation": _FT,
    "health": _FT,
    "execution": MISSION_ROLES,  # Today's Mission + Roadmap
    "funding": FUNDING_ROLES,
    "custom": _FT,
}
HUB_LABELS = {
    "finance": "Finance",
    "sales": "Sales",
    "marketing": "Marketing",
    "validation": "Validation",
    "health": "Health Score",
    "execution": "Mission & Roadmap",
    "funding": "Funding",
    "custom": "Your own metrics",
}
HUB_ORDER = list(HUB_ROLES)
# Where the hub's own page lives in the FE (the "Open" link).
HUB_PATHS = {
    "finance": "/finance",
    "sales": "/sales",
    "marketing": "/marketing",
    "validation": "/validation",
    "health": "/health-score",
    "execution": "/roadmap",
    "funding": "/funding",
    "custom": "/analytics-reports",
}


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str  # money | count | percent | months | days | score
    hub: str
    kind: str  # flow (sum over the period) | point (value as of the period end)
    definition: str
    good: str = "up"  # up | down | neutral: which direction is good news (delta tone)
    source_note: str = ""
    group_by: tuple[str, ...] = field(default_factory=tuple)  # slice B+
    custom: bool = False


STATIC: tuple[Metric, ...] = (
    Metric(
        "finance.revenue",
        "Revenue",
        "money",
        "finance",
        "flow",
        "Money in from customers over the period. Funding and payout transfers are excluded.",
        source_note="Finance ledger",
    ),
    Metric(
        "finance.expenses",
        "Expenses",
        "money",
        "finance",
        "flow",
        "All money out over the period.",
        good="down",
        source_note="Finance ledger",
    ),
    Metric(
        "finance.net_burn",
        "Net burn",
        "money",
        "finance",
        "flow",
        "Expenses minus revenue over the period, never below zero.",
        good="down",
        source_note="Finance ledger",
    ),
    Metric(
        "finance.cash_balance",
        "Cash balance",
        "money",
        "finance",
        "point",
        "All money in minus all money out, up to the end of the period.",
        source_note="Finance ledger",
    ),
    Metric(
        "finance.runway_months",
        "Runway",
        "months",
        "finance",
        "point",
        "Cash on hand divided by the trailing three-month monthly burn.",
        source_note="Finance runway",
    ),
    Metric(
        "sales.pipeline_value",
        "Open pipeline",
        "money",
        "sales",
        "point",
        "Value of deals still in an open stage.",
        source_note="Sales pipeline",
    ),
    Metric(
        "sales.deals_won",
        "Deals won",
        "count",
        "sales",
        "flow",
        "Deals closed as won in the period.",
        source_note="Sales pipeline",
    ),
    Metric(
        "sales.closed_revenue",
        "Closed-won value",
        "money",
        "sales",
        "flow",
        "Total value of deals won in the period.",
        source_note="Sales pipeline",
    ),
    Metric(
        "sales.win_rate",
        "Win rate",
        "percent",
        "sales",
        "flow",
        "Won divided by won plus lost, for deals closed in the period.",
        source_note="Sales pipeline",
    ),
    Metric(
        "sales.avg_cycle_days",
        "Sales cycle",
        "days",
        "sales",
        "flow",
        "Average days from deal created to won, for deals won in the period.",
        good="down",
        source_note="Sales pipeline",
    ),
    Metric(
        "marketing.impressions",
        "Impressions",
        "count",
        "marketing",
        "flow",
        "Recorded impressions across channels.",
        source_note="Marketing metrics",
    ),
    Metric(
        "marketing.clicks",
        "Clicks",
        "count",
        "marketing",
        "flow",
        "Recorded clicks across channels.",
        source_note="Marketing metrics",
    ),
    Metric(
        "marketing.conversions",
        "Conversions",
        "count",
        "marketing",
        "flow",
        "Recorded conversions across channels.",
        source_note="Marketing metrics",
    ),
    Metric(
        "marketing.ctr",
        "Click-through rate",
        "percent",
        "marketing",
        "flow",
        "Clicks divided by impressions.",
        source_note="Marketing metrics",
    ),
    Metric(
        "validation.assumptions_validated_pct",
        "Assumptions validated",
        "percent",
        "validation",
        "point",
        "Share of your assumptions marked validated.",
        source_note="Validation Hub",
    ),
    Metric(
        "health.score",
        "Health Score",
        "score",
        "health",
        "point",
        "Your current Health Score out of 100.",
        source_note="Health Score",
    ),
    Metric(
        "execution.tasks_done",
        "Mission tasks done",
        "count",
        "execution",
        "flow",
        "Today's Mission tasks completed in the period.",
        source_note="Today's Mission",
    ),
    Metric(
        "execution.milestones_done_pct",
        "Roadmap milestones done",
        "percent",
        "execution",
        "point",
        "Share of roadmap milestones marked done.",
        source_note="Roadmap",
    ),
    Metric(
        "funding.investors_active",
        "Active investors",
        "count",
        "funding",
        "point",
        "Investors in your pipeline that have not passed.",
        source_note="Funding pipeline",
    ),
)
BY_KEY = {m.key: m for m in STATIC}

# The four cards the Overview opens with, in order.
HEADLINE = ("finance.revenue", "finance.net_burn", "finance.runway_months", "sales.pipeline_value")


def can_see(role: MembershipRole, hub: str) -> bool:
    return role in ANALYTICS_ROLES and role in HUB_ROLES.get(hub, frozenset())


def visible_hubs(role: MembershipRole) -> list[str]:
    return [h for h in HUB_ORDER if can_see(role, h)]
