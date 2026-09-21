from typing import Any

from app.platform.llm import LLMMessage


def dashboard_briefing_schema() -> dict[str, Any]:
    """Strict schema: an object with three required prose fields."""
    return {
        "type": "object",
        "properties": {
            "briefing": {"type": "string"},
            "risks": {"type": "string"},
            "opportunities": {"type": "string"},
        },
        "required": ["briefing", "risks", "opportunities"],
        "additionalProperties": False,
    }


def build_dashboard_briefing_messages(
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
    health_score: int | None,
    health_band: str | None,
    mission_total: int,
    mission_done: int,
    upcoming_count: int,
    tasks_done_week: int,
) -> list[LLMMessage]:
    """Prompt for the dashboard's daily briefing/risks/opportunities. Business + state
    context only -- no PII."""
    system = (
        "You are an AI co-founder writing a founder's daily dashboard panel. Return three short, "
        "concrete, encouraging-but-honest paragraphs: 'briefing' (a 2-3 sentence read on where the "
        "startup stands today), 'risks' (the most important things to watch, 1-2 sentences), and "
        "'opportunities' (concrete quick wins or openings, 1-2 sentences). No preamble, no headings."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\n"
        f"Health score: {health_score if health_score is not None else 'n/a'} "
        f"(band: {health_band or 'n/a'}).\n"
        f"Today's mission: {mission_done}/{mission_total} tasks done.\n"
        f"Deadlines in the next 7 days: {upcoming_count}.\n"
        f"Tasks completed this week: {tasks_done_week}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
