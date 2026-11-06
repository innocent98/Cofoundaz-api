"""`GET /ai/agents`: the bench. The four MVP agents are live; the rest are listed as coming soon so
the page can show the whole bench honestly. Names, categories and one-liners for the not-yet-built
agents are copied from the FE's `app/(dashboard)/ai/agents/page.tsx` (the PRD's "Agent Bench table"
is not in the PRD file, so that copy is unverified against it)."""

from typing import Any

from app.services.ai_cofounder.agents import AGENTS

_LIVE: dict[str, tuple[str, str]] = {
    "strategist": ("Business Builder", "Sharpens your model, plan, and positioning."),
    "finance": ("Finance Hub", "Runway, forecasts, and the hard money calls."),
    "legal": ("Legal & Compliance", "Contracts, formation, and staying covered."),
    "mentor": ("Across everything", "A steady voice for the founder behind the company."),
}
_SOON: tuple[tuple[str, str, str, str], ...] = (
    ("marketing", "Marketing Advisor", "Marketing Hub", "Campaigns, copy, and channel strategy."),
    ("sales", "Sales Coach", "Sales Hub", "Pipeline, objections, and closing with confidence."),
    (
        "fundraising",
        "Fundraising Copilot",
        "Funding Hub",
        "Investors, the data room, and running the raise.",
    ),
    (
        "readiness",
        "Readiness Coach",
        "Investor Readiness",
        "Your deck, your story, and the tough questions.",
    ),
    (
        "validation",
        "Validation Scientist",
        "Validation Hub",
        "Experiments that prove it before you build.",
    ),
    ("product", "Product Manager", "Roadmap", "Turns feedback into the right next build."),
    (
        "insight",
        "Insight Synthesizer",
        "Validation Hub",
        "Finds the patterns hiding in your feedback.",
    ),
)


def list_agents() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, agent in AGENTS.items():
        category, line = _LIVE[key]
        out.append(
            {
                "key": key,
                "name": agent.name,
                "category": category,
                "description": line,
                "topic": agent.topic,
                "available": True,
                "status": "available",
            }
        )
    for key, name, category, line in _SOON:
        out.append(
            {
                "key": key,
                "name": name,
                "category": category,
                "description": line,
                "topic": None,
                "available": False,
                "status": "coming_soon",
            }
        )
    return out
