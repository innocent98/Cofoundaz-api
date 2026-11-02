from app.platform.llm import LLMMessage
from app.services.ai_preferences import tone_instruction


def build_cofounder_messages(
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
    description: str | None,
    goals: list[str],
    history: list[tuple[str, str]],
    finance_context: str | None = None,
    tone: str | None = None,
) -> list[LLMMessage]:
    """Prompt for an AI Co-Founder chat turn.

    `history` is the prior conversation as (role, content) pairs in order, ending with
    the founder's latest message. Workspace context is business-level only (no PII).
    """
    goal_lines = "; ".join(goals) if goals else "not specified"
    system = (
        "You are the founder's AI co-founder at Cofoundaz: a candid, practical partner who knows "
        "their business. Give direct, specific, actionable advice in a warm but no-nonsense voice. "
        "Keep replies concise and concrete, and ask a clarifying question only when it is essential. "
        "Do not invent facts about their company beyond the context you are given.\n"
        f"Startup: {name or 'unspecified'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}. One-liner: {description or 'unspecified'}. "
        f"Current focus goals: {goal_lines}."
    )
    system += "\n" + tone_instruction(tone)
    if finance_context:
        system += "\n" + finance_context
    messages = [LLMMessage(role="system", content=system)]
    for role, content in history:
        if role in ("user", "assistant") and content:
            messages.append(LLMMessage(role=role, content=content))  # type: ignore[arg-type]
    return messages
