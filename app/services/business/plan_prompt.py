import json
from typing import Any

from app.platform.llm import LLMMessage
from app.services.business.plan_defs import DEFAULT_OPTIONS, PlanSection

_AUDIENCE = {
    "investors": "an investor audience (growth, market size, returns)",
    "bank": "a bank lender audience (repayment ability, cash flow, collateral, risk)",
    "internal": "an internal team audience (plain priorities and decisions, no pitch tone)",
}
_LENGTH = {
    "concise": "Keep it concise: about 150 to 250 words (the whole plan is 10 to 15 pages).",
    "full": "Be thorough: about 500 to 800 words (the whole plan is 25 to 40 pages).",
}


def build_section_messages(
    section: PlanSection, context: dict[str, Any], options: dict[str, Any] | None = None
) -> list[LLMMessage]:
    """One plan section's prompt. Returns prose markdown for the body only (no heading).
    `options` (PRD 08.11): audience bank|investors|internal, length concise|full."""
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    system = (
        f"You are writing one section of a startup's business plan for "
        f"{_AUDIENCE[opts['audience']]}. Write clear, concrete markdown prose for the section "
        "BODY only, no section heading, no preamble. Use the provided context; do not invent "
        f"facts not supported by it. {_LENGTH[opts['length']]}"
    )
    user = (
        f"Section: {section.heading}\nWhat to cover: {section.guidance}\n\n"
        f"Startup context (JSON):\n{json.dumps(context, ensure_ascii=False)}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
