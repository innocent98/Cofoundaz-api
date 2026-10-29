"""Notification categories and classes: the single source for event -> category, priority and
inbox filter (PRD 20).

A category is a row of the Preferences grid: it carries an in-app switch and an email switch.
`CRITICAL_TYPES` (PRD 20.2: payment failed, runway low, compliance overdue, signature requests)
ignore every switch, the digest and quiet hours: they are always delivered in-app and by email.
"""

CATEGORIES: tuple[str, ...] = (
    "documents",
    "business",
    "roadmap_missions",
    "health_assessment",
    "team",
    "finance",
    "marketing",
    "sales",
    "validation",
    "academy",
    "ai_insights",
    "billing",
)

# Titles and one-line descriptions for the Preferences grid (the API is the source, the FE renders).
CATEGORY_META: dict[str, tuple[str, str]] = {
    "documents": ("Documents", "Shares, versions and e-signature updates."),
    "business": ("Business Builder", "Suggestions, plans and completed artifacts."),
    "roadmap_missions": ("Roadmap & missions", "Re-plans, milestones, missions and streaks."),
    "health_assessment": ("Health & assessment", "Score changes and assessments."),
    "team": ("Team", "New members and comments that mention you."),
    "finance": ("Finance", "Runway, invoices and payments."),
    "marketing": ("Marketing", "Posts published and campaign results."),
    "sales": ("Sales", "Deals won or stalled and sequence sending."),
    "validation": ("Validation", "Smoke-test milestones and new customer insights."),
    "academy": ("Academy", "Courses completed and recommended."),
    "ai_insights": ("AI & insights", "Your daily briefing and KPI alerts."),
    "billing": ("Billing", "Plan changes and usage."),
}

EVENT_CATEGORY: dict[str, str] = {
    "document.shared": "documents",
    "document.version.created": "documents",
    "document.grant.created": "documents",
    "document.signature.requested": "documents",
    "document.signature.signed": "documents",
    "document.signature.completed": "documents",
    "business.suggestion.created": "business",
    "business.suggestion.approved": "business",
    "business.suggestion.rejected": "business",
    "business.artifact.completed": "business",
    "business.plan.generated": "business",
    "roadmap.replanned": "roadmap_missions",
    "roadmap.milestone.completed": "roadmap_missions",
    "mission.completed": "roadmap_missions",
    "mission.streak.milestone": "roadmap_missions",
    "healthscore.dropped": "health_assessment",
    "healthscore.record": "health_assessment",
    "assessment.completed": "health_assessment",
    "workspace.member.joined": "team",
    "team.comment.mentioned": "team",
    "mission.ready": "roadmap_missions",
    "roadmap.milestone.overdue": "roadmap_missions",
    "roadmap.milestone.due_soon": "roadmap_missions",
    "assessment.quarterly.due": "health_assessment",
}

# Types not listed above are classified by their prefix, so a new module's events (Billing, ...)
# land in the right row without touching this file. Journal nudges have no row on purpose: they
# are in-app only and have their own switches on the Journal page.
PREFIX_CATEGORY: dict[str, str] = {
    "finance.": "finance",
    "kpi.": "ai_insights",
    "dashboard.": "ai_insights",
    "marketing.": "marketing",
    "sales.": "sales",
    "validation.": "validation",
    "learning.": "academy",
    "billing.": "billing",
}

# Email defaults (opt-out model, spec D3) for the five original rows; the rows added later start
# OFF (D-NOT-2) so nobody begins receiving new kinds of email unasked. In-app is ON everywhere.
CATEGORY_DEFAULTS: dict[str, bool] = {
    **dict.fromkeys(CATEGORIES, False),
    **dict.fromkeys(
        ("documents", "business", "roadmap_missions", "health_assessment", "team", "billing"),
        True,
    ),
}

# PRD 20.2 locked-on alerts (D-NOT-1). Always in-app and email, regardless of any switch.
# Only types whose recipients are already role-restricted belong here: a critical alert
# ignores every opt-out, so it must never reach roles outside its audience.
CRITICAL_TYPES: frozenset[str] = frozenset(
    {
        "billing.payment.failed",
        "finance.runway.low",
        "finance.invoice.overdue",
        "document.signature.requested",
        "sales.sending.paused",
    }
)

# Inbox filter chips (PRD 20.1: All / Mentions / AI / Deadlines / Billing, plus Unread / Urgent).
FILTER_TYPES: dict[str, frozenset[str]] = {
    "mentions": frozenset({"team.comment.mentioned"}),
    "ai": frozenset(
        {
            "dashboard.briefing.ready",
            "business.plan.generated",
            "business.suggestion.created",
            "business.artifact.completed",
            "validation.insight.detected",
            "learning.course.recommended",
            "roadmap.replanned",
        }
    ),
    "deadlines": frozenset(
        {
            "roadmap.milestone.due_soon",
            "roadmap.milestone.overdue",
            "finance.invoice.overdue",
            "assessment.quarterly.due",
        }
    ),
}
FILTERS: tuple[str, ...] = ("all", "unread", "urgent", "mentions", "ai", "deadlines", "billing")

_MODULE_BY_PREFIX: dict[str, str] = {
    "mission": "missions",
    "roadmap": "roadmap",
    "healthscore": "health",
    "assessment": "health",
    "workspace": "team",
    "team": "team",
    "document": "documents",
    "business": "business",
    "finance": "finance",
    "kpi": "dashboard",
    "dashboard": "dashboard",
    "marketing": "marketing",
    "sales": "sales",
    "validation": "validation",
    "learning": "academy",
    "billing": "billing",
    "journal": "journal",
}


def category_for(event: str) -> str | None:
    """The category an event emails under, or None (in-app only)."""
    hit = EVENT_CATEGORY.get(event)
    if hit is not None:
        return hit
    for prefix, category in PREFIX_CATEGORY.items():
        if event.startswith(prefix):
            return category
    return None


def is_critical(event: str) -> bool:
    return event in CRITICAL_TYPES


def module_for(event: str) -> str:
    """The product area an inbox row belongs to (its icon), from the type's first segment."""
    return _MODULE_BY_PREFIX.get(event.split(".", 1)[0], "other")


def types_for_filter(name: str) -> frozenset[str] | None:
    """Exact types behind a type-based filter chip; billing is prefix-based (see service)."""
    return FILTER_TYPES.get(name)
