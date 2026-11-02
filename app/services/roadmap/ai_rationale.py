from app.platform.llm import LLMMessage


def build_roadmap_rationale_messages(
    *,
    stage: str | None,
    name: str | None,
    industry: str | None,
    changes: list[dict],
) -> list[LLMMessage]:
    """Prompt for a holistic 'why we re-planned' rationale. Business context only -- no PII."""
    lines = "\n".join(
        f"- {c.get('title', 'a milestone')}: {c.get('old_due', '?')} -> {c.get('new_due', '?')} "
        f"({c.get('reason', '')})"
        for c in changes
    )
    system = (
        "You are a startup coach explaining a roadmap re-plan to a founder. Write a short (2-3 "
        "sentence) reassuring but honest rationale: why these milestone dates moved and what it "
        "means for momentum. No preamble, no headings, no bullet list."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nShifted milestones:\n{lines or '(none)'}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
