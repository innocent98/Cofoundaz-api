from typing import Any

from app.db.models.enums import StartupStage
from app.platform.llm import LLMMessage


def learning_reason_schema() -> dict[str, Any]:
    """Strict OpenAI JSON schema: an object with a single `reason` string."""
    return {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "additionalProperties": False,
    }


def build_learning_reason_messages(
    *, stage: StartupStage | None, course_titles: list[str]
) -> list[LLMMessage]:
    """Prompt for the one-line 'why this shelf' note above recommended courses. No PII."""
    titles = "\n".join(f"- {t}" for t in course_titles) or "- (getting-started courses)"
    stage_label = stage.value if stage is not None else "early"
    system = (
        "You are a startup coach writing the single one-line explanation shown above a shelf of "
        "recommended courses. Write one warm sentence (max 200 characters) that says why these "
        "courses fit this founder right now. One sentence only, no lists."
    )
    user = f"Founder stage: {stage_label}.\nRecommended courses:\n{titles}"
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
