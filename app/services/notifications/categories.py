"""Notification categories: the coarse groups the FE toggles email by (spec D3).

Single source of truth for event -> category. In-app delivery ignores this
(always on); it governs only the email channel.
"""

CATEGORIES: tuple[str, ...] = (
    "documents", "business", "roadmap_missions", "health_assessment", "team",
)

EVENT_CATEGORY: dict[str, str] = {
    "document.shared": "documents",
    "document.signature.requested": "documents",
    "document.signature.signed": "documents",
    "document.signature.completed": "documents",
    "business.suggestion.created": "business",
    "business.suggestion.approved": "business",
    "business.suggestion.rejected": "business",
    "business.artifact.completed": "business",
    "roadmap.replanned": "roadmap_missions",
    "roadmap.milestone.completed": "roadmap_missions",
    "mission.completed": "roadmap_missions",
    "mission.streak.milestone": "roadmap_missions",
    "healthscore.dropped": "health_assessment",
    "assessment.completed": "health_assessment",
    "workspace.member.joined": "team",
}

# All ON (opt-out model, spec D3). New categories default ON here.
CATEGORY_DEFAULTS: dict[str, bool] = {c: True for c in CATEGORIES}


def category_for(event: str) -> str | None:
    """The category an event emails under, or None (⇒ in-app only)."""
    return EVENT_CATEGORY.get(event)
