from collections.abc import Sequence
from typing import Any

from app.platform.llm import LLMMessage
from app.services.health_score.config import DIMENSION_LABELS, RECOMMENDATION_CATALOG


def catalog_bodies() -> dict[str, str]:
    """Every catalog recommendation key -> its default body text."""
    return {e["key"]: e["body"] for entries in RECOMMENDATION_CATALOG.values() for e in entries}


def health_recommendation_schema(keys: Sequence[str]) -> dict[str, Any]:
    """Strict schema: an object with a `recommendations` array of {key, body}. `key` is
    constrained to the given pending keys (stricter for OpenAI; usable by the stub)."""
    return {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "maxItems": len(keys),
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": list(keys)},
                        "body": {"type": "string"},
                    },
                    "required": ["key", "body"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["recommendations"],
        "additionalProperties": False,
    }


def build_health_recommendation_messages(
    recs: Sequence[tuple[str, str, str]],
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
) -> list[LLMMessage]:
    """Prompt for a personalized body per pending recommendation. recs = (key, dimension, title).
    Business context only -- no PII."""
    lines = "\n".join(
        f"- key {key} ({DIMENSION_LABELS.get(dim, dim)}): {title}" for key, dim, title in recs
    )
    system = (
        "You are a startup advisor personalizing health-score recommendations. For each "
        "recommendation write a concrete, actionable body of 1-2 sentences tailored to this "
        "startup. Return exactly one body per recommendation, keyed by its key."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nRecommendations:\n{lines}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
