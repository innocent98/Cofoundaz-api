from collections.abc import Sequence
from typing import Any

from app.platform.llm import LLMMessage


def mission_reason_schema(n: int) -> dict[str, Any]:
    """Strict OpenAI JSON schema: an object with a `reasons` array (<= n) of {order, reason}."""
    return {
        "type": "object",
        "properties": {
            "reasons": {
                "type": "array",
                "maxItems": n,
                "items": {
                    "type": "object",
                    "properties": {
                        "order": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["order", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["reasons"],
        "additionalProperties": False,
    }


def build_mission_reason_messages(
    tasks: Sequence[tuple[int, str]],
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
) -> list[LLMMessage]:
    """Prompt for the one-line 'why this task today' note per mission task. No PII."""
    lines = "\n".join(f"- order {order}: {title}" for order, title in tasks)
    system = (
        "You are a startup coach writing the one-line 'why this task today' note shown beside each "
        "daily mission task. Each reason is a single motivating sentence (max 300 characters) that "
        "ties the task to the founder's momentum. Return exactly one reason per task, keyed by its "
        "order."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nToday's tasks:\n{lines}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
