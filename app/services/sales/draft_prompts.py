from typing import Any

from app.platform.llm import LLMMessage
from app.services.sales.coach_context import CONTEXT_RULES
from app.services.sales.coach_prompts import NO_DASHES

_SHAPE = (
    "Reply with a `subject` (under 70 characters) and a plain text `body` (60 to 140 words, short "
    "paragraphs, one clear ask, no signature block and no unsubscribe text because the system "
    "adds both). Use ONLY these merge tags where they help: {{first_name}}, {{last_name}}, "
    "{{company}}, {{sender_name}}. Never invent facts, numbers, customers or results."
)


def draft_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
        "required": ["subject", "body"],
        "additionalProperties": False,
    }


def build_step_messages(*, context: str) -> list[LLMMessage]:
    system = (
        "You write one email of a cold outreach sequence for a startup founder. The context says "
        "which step this is (step 1 is the first touch; later steps are polite follow-ups that "
        "add something new and never guilt-trip). If the step already has a draft, improve it "
        "and keep the author's intent. " + _SHAPE + NO_DASHES + CONTEXT_RULES
    )
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=context),
    ]


def build_follow_up_messages(*, context: str) -> list[LLMMessage]:
    system = (
        "You write a short follow-up email from a founder to the contact of a live deal, picking "
        "up from the deal's recent history and proposing the single next step. "
        + _SHAPE
        + NO_DASHES
        + CONTEXT_RULES
    )
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=context),
    ]
