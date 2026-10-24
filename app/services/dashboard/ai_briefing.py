from typing import Any

from app.platform.llm import LLMMessage

# Modules the AI may point a risk/opportunity/action at. The server maps each to a route
# (app/services/dashboard/briefing.py::MODULE_HREFS), so the model never invents a URL.
BRIEFING_MODULES: tuple[str, ...] = (
    "mission",
    "roadmap",
    "health",
    "validation",
    "business_builder",
    "marketing",
    "finance",
    "documents",
    "team",
    "assessment",
)
SEVERITIES: tuple[str, ...] = ("high", "medium", "low")
MAX_ITEMS = 3


def _row(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def dashboard_briefing_schema() -> dict[str, Any]:
    """Strict schema: the briefing prose plus structured risk/opportunity rows and 1-3
    proposed actions (PRD 02.1: per-action "Do it" / "Tell me more")."""
    module = {"type": "string", "enum": list(BRIEFING_MODULES)}
    return _row(
        {
            "briefing": {"type": "string"},
            "risks": {
                "type": "array",
                "maxItems": MAX_ITEMS,
                "items": _row(
                    {
                        "severity": {"type": "string", "enum": list(SEVERITIES)},
                        "text": {"type": "string"},
                        "module": module,
                    }
                ),
            },
            "opportunities": {
                "type": "array",
                "maxItems": MAX_ITEMS,
                "items": _row({"text": {"type": "string"}, "module": module}),
            },
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_ITEMS,
                "items": _row(
                    {
                        "label": {"type": "string"},
                        "kind": {"type": "string", "enum": ["task", "navigate"]},
                        "module": module,
                    }
                ),
            },
        }
    )


def build_dashboard_briefing_messages(
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
    health_band: str | None,
    mission_total: int,
    mission_done: int,
    upcoming_count: int,
    tasks_done_week: int,
    runway_months: float | None = None,
    runway_status: str | None = None,
    monthly_revenue: str | None = None,
) -> list[LLMMessage]:
    """Prompt for the dashboard's daily briefing. Business + state context only, no PII."""
    system = (
        "You are an AI co-founder writing a founder's daily dashboard panel. Be concrete, "
        "encouraging but honest, and grounded ONLY in the facts given. Return: 'briefing' (a "
        "2-3 sentence read on where the startup stands today); 'risks' (0-3 rows, most "
        "important first, each one short sentence with a severity and the module where the "
        "founder would act on it; return an empty list if nothing is genuinely at risk); "
        "'opportunities' (0-3 rows, concrete quick wins, each one short sentence with its "
        "module); 'actions' (1-3 next steps for today, each a short imperative label under 70 "
        "characters that IS the task itself, e.g. 'Interview 3 target customers', never a "
        "meta-instruction like 'Add a task to...'. Use kind 'task' for something the founder "
        "does (it is added to today's mission as written) and 'navigate' only for 'Review X' "
        "steps). The dashboard shows the live Health Score right next to this text and it changes "
        "during the day, so never state the Health Score number or any change in it; you may "
        "refer to the health band in words. No preamble, no headings, no invented numbers."
    )
    finance = (
        f"Runway: {runway_months} months.\n"
        if runway_months is not None
        else f"Runway: {runway_status or 'not tracked'}.\n"
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\n"
        f"Health band: {health_band or 'n/a'}.\n"
        f"Today's mission: {mission_done}/{mission_total} tasks done.\n"
        f"Deadlines in the next 7 days: {upcoming_count}.\n"
        f"Tasks completed this week: {tasks_done_week}.\n"
        f"{finance}"
        f"Average monthly revenue, funding excluded (last 3 months): {monthly_revenue or 'not tracked'}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
