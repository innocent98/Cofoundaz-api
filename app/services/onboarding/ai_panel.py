from app.platform.llm import LLMMessage


def _templated_panel(industry: str, stage: str) -> str:
    """Instant fallback shown until the AI job overwrites it (reuses the retired stub's wording)."""
    return f"Got it — a {industry} startup at the {stage} stage. Let's calibrate your workspace."


def build_onboarding_panel_messages(
    *,
    industry: str | None,
    stage: str | None,
    goals: list[str],
) -> list[LLMMessage]:
    """Prompt for the onboarding calibration panel. Business signals only -- no PII."""
    goal_lines = "; ".join(goals) if goals else "(not specified)"
    system = (
        "You are an AI co-founder greeting a founder as they finish onboarding. Write a short "
        "(2-3 sentence) warm, concrete calibration message: reflect back what you understand about "
        "their startup and set expectations for how you'll help. No preamble, no headings."
    )
    user = (
        f"Industry: {industry or 'unspecified'}. Stage: {stage or 'unspecified'}. "
        f"Goals: {goal_lines}. Write the calibration message."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
