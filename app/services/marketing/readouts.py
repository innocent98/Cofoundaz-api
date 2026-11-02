"""Weekly AI readout of an active campaign (PRD 10: "AI weekly readout card").

The figures come from `readout_figures` (computed in Python); the prompt carries only those
pre-formatted figures, no campaign name and no free text from the founder.
"""

import uuid
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.marketing import Campaign, CampaignReadout
from app.platform.llm_budget import metered_complete_json
from app.schemas.marketing import ReadoutResponse
from app.services.marketing.ai_prompts import build_readout_messages, readout_schema
from app.services.marketing.readout_figures import (
    fallback_output,
    llm_facts,
    unverified_numbers,
)


def serialize_readout(r: CampaignReadout) -> ReadoutResponse:
    output = dict(r.output or {})
    figures = output.pop("figures", None)
    return ReadoutResponse(
        id=r.id,
        campaign_id=r.campaign_id,
        week_start=r.week_start,
        status=r.status,
        output=output,
        figures=figures,
        error=r.error,
        created_at=r.created_at,
    )


def list_readouts(
    db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID, limit: int = 12
) -> list[CampaignReadout]:
    return (
        db.query(CampaignReadout)
        .filter_by(startup_id=startup_id, campaign_id=campaign_id)
        .order_by(CampaignReadout.week_start.desc())
        .limit(limit)
        .all()
    )


def _clean(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": str(result.get("headline") or ""),
        "summary": str(result.get("summary") or ""),
        "wins": [str(x) for x in (result.get("wins") or [])][:3],
        "watch": [str(x) for x in (result.get("watch") or [])][:3],
        "next_step": str(result.get("next_step") or ""),
    }


def generate_verified_output(
    db: Session, campaign: Campaign, figures: dict[str, Any]
) -> dict[str, Any] | None:
    """The readout prose, with every number checked against `figures`.

    The model is shown only the pre-formatted figures. If its prose states any other number it is
    asked once more (told which numbers were rejected); if the retry fails too, or the day's AI
    budget runs out on the retry, the deterministic template summary is used instead. Prose with
    an unverified number is never returned. None means the budget was already spent before the
    first attempt. `source` says which path produced the text."""
    facts = llm_facts(figures, campaign)
    rejected: list[str] | None = None
    for attempt in range(2):
        result = metered_complete_json(
            db,
            campaign.startup_id,
            build_readout_messages(facts=facts, rejected=rejected),
            schema=readout_schema(),
            max_tokens=settings.LLM_MAX_TOKENS,
        )
        if result is None:
            if attempt == 0:
                return None
            break
        output = _clean(result)
        bad = unverified_numbers(output, facts)
        if not bad:
            return {**output, "source": "ai"}
        rejected = sorted(set(bad))
        logger.warning(
            "marketing readout: model stated unverified numbers {} (attempt {})",
            rejected,
            attempt + 1,
        )
    return {**fallback_output(figures), "source": "template"}
