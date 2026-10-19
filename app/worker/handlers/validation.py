"""Validation Hub AI jobs (Module 09)."""

import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.db.models.validation import Assumption
from app.platform.llm import LLMMessage
from app.platform.llm_budget import metered_complete_json
from app.services.validation.smoke import METRIC_TYPES
from app.worker.runner import register_handler

_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "headline": {"type": "string"},
        "subheadline": {"type": "string"},
        "cta_text": {"type": "string"},
        "metric_type": {"type": "string", "enum": list(METRIC_TYPES)},
        "target_conversion": {"type": "integer"},
        "rationale": {"type": "string"},
    },
    "required": [
        "name",
        "headline",
        "subheadline",
        "cta_text",
        "metric_type",
        "target_conversion",
        "rationale",
    ],
    "additionalProperties": False,
}


def build_experiment_draft_messages(startup: Startup, assumption: Assumption) -> list[LLMMessage]:
    system = (
        "You help early-stage founders test one risky assumption with a smoke test: a simple "
        "landing page that offers the product before it exists and measures real intent. Write "
        "plain, specific copy a customer would understand. No jargon, no hype, no em or en dashes. "
        "Pick the success metric that best proves the assumption: email_signup (they want it), "
        "cta_click (it catches attention) or preorder_click (they would pay). Set "
        "target_conversion as a whole-number percentage; typical cold-traffic bars are 5 to 10."
    )
    user = (
        f"Startup: {startup.name or 'an early-stage startup'}"
        f" ({startup.industry or 'industry not set'}, {startup.stage.value if startup.stage else 'idea'} stage).\n"
        f"Assumption to test (risk {assumption.risk.value}): {assumption.statement}\n"
        "Draft the page: a short internal name for the test, a headline (max 10 words), a "
        "subheadline (one sentence), the button text, the success metric, the target conversion, "
        "and one sentence on why this test proves or kills the assumption."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50].strip("-")
    return slug if len(slug) >= 3 else ""


def handle_experiment_draft(db: Session, job: Job) -> None:
    """Draft a smoke test for an assumption. The draft lives in `job.result` only; nothing is
    saved (PRD 09.6 "Design experiment"). Statuses: ready / gone / over_budget."""
    startup = db.get(Startup, job.payload["startup_id"])
    assumption = (
        db.query(Assumption)
        .filter_by(id=job.payload["assumption_id"], startup_id=job.payload["startup_id"])
        .first()
    )
    if startup is None or assumption is None:
        job.result = {"status": "gone", "assumption_id": job.payload["assumption_id"]}
        return
    out = metered_complete_json(
        db,
        startup.id,
        build_experiment_draft_messages(startup, assumption),
        schema=_DRAFT_SCHEMA,
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if out is None:
        job.result = {"status": "over_budget", "assumption_id": str(assumption.id)}
        return
    target = out.get("target_conversion")
    target = min(50, max(1, int(target))) if isinstance(target, int | float) and target else 8
    metric = out.get("metric_type") if out.get("metric_type") in METRIC_TYPES else "email_signup"
    name = str(out.get("name") or "Smoke test").strip()[:255]
    job.result = {
        "status": "ready",
        "assumption_id": str(assumption.id),
        "draft": {
            "name": name,
            "headline": str(out.get("headline") or "").strip(),
            "subheadline": str(out.get("subheadline") or "").strip(),
            "cta_text": str(out.get("cta_text") or "Join the waitlist").strip(),
            "metric_type": metric,
            "target_conversion": target,
            "rationale": str(out.get("rationale") or "").strip(),
            "slug_suggestion": _slugify(startup.name + " " + name if startup.name else name),
            "assumption_ids": [str(assumption.id)],
        },
    }


register_handler("validation.experiment.draft", handle_experiment_draft)
