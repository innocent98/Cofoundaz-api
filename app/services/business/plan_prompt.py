import json
from typing import Any

from app.platform.llm import LLMMessage
from app.services.business.plan_defs import PlanSection


def build_section_messages(section: PlanSection, context: dict[str, Any]) -> list[LLMMessage]:
    """One plan section's prompt. Returns prose markdown for the body only (no heading)."""
    system = (
        "You are writing one section of a startup's business plan for an investor audience. "
        "Write clear, concrete markdown prose for the section BODY only — no section heading, no "
        "preamble. Use the provided context; do not invent facts not supported by it."
    )
    user = (
        f"Section: {section.heading}\nWhat to cover: {section.guidance}\n\n"
        f"Startup context (JSON):\n{json.dumps(context, ensure_ascii=False)}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
