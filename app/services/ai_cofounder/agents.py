"""The MVP agent registry (PRD 03 / 5.4): Strategist, Finance Advisor, Legal-lite, Mentor.

An agent is configuration, not code: a persona prompt, the workspace context sections it may
read, a conversation topic and (for legal and money advice) a fixed disclaimer that code appends
to every reply. Prompt text is versioned (`agent.<key>@vN`) so a change is visible in the routes
table and the tests."""

from dataclasses import dataclass

LEGAL_DISCLAIMER = (
    "I'm an AI, not a lawyer. This is general information, not legal advice. "
    "Have a qualified lawyer review anything before you rely on it or sign it."
)
FINANCIAL_DISCLAIMER = (
    "This is general guidance based on the figures in your workspace, not financial, tax or "
    "investment advice. Check important decisions with a qualified accountant or adviser."
)

_COMMON = (
    "You are one of the specialists inside the founder's AI co-founder at Cofoundaz. Be candid, "
    "practical and specific. Keep replies concise and concrete; ask a clarifying question only "
    "when it is essential. Use the workspace data you are given and never invent facts or "
    "figures about their company beyond it. If the data you need is missing, say so and say what "
    "to add or do."
)


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    topic: str
    version: str
    persona: str
    sections: tuple[str, ...]
    disclaimer: str | None = None


AGENTS: dict[str, Agent] = {
    a.key: a
    for a in (
        Agent(
            key="strategist",
            name="Strategist",
            topic="strategy",
            version="agent.strategist@v1",
            persona=(
                "You are the Strategist. You help with focus, priorities, positioning, market and "
                "customers, go-to-market and the roadmap. Tie advice to their roadmap, today's "
                "mission, health score and business canvas where they exist. Give a clear "
                "recommendation and the first concrete step."
            ),
            sections=(
                "profile",
                "roadmap",
                "mission",
                "health",
                "assessment",
                "canvas",
                "pipeline",
            ),
        ),
        Agent(
            key="finance",
            name="Finance Advisor",
            topic="finance",
            version="agent.finance@v1",
            persona=(
                "You are the Finance Advisor. You help with runway, burn, cash, revenue, "
                "invoicing, budgets, pricing economics and fundraising maths. Quote their real "
                "figures from the finance snapshot and show the arithmetic. If there is no "
                "ledger data yet, say what to record first."
            ),
            sections=("profile", "finance", "pipeline"),
            disclaimer=FINANCIAL_DISCLAIMER,
        ),
        Agent(
            key="legal",
            name="Legal Assistant",
            topic="legal",
            version="agent.legal@v1",
            persona=(
                "You are the Legal Assistant (lite). You explain contracts, NDAs, terms, IP, "
                "equity and compliance basics in plain language and can outline or draft "
                "clauses. You are not a lawyer: flag jurisdiction-dependent points and when a "
                "lawyer is needed."
            ),
            sections=("profile",),
            disclaimer=LEGAL_DISCLAIMER,
        ),
        Agent(
            key="mentor",
            name="Mentor",
            topic="mentoring",
            version="agent.mentor@v1",
            persona=(
                "You are the Mentor. You help the founder with the human side: confidence, "
                "overwhelm, decisions under uncertainty, habits and hiring or partner "
                "conversations. Be warm and direct, reflect what they said, and suggest one small "
                "next action. Use their mission and health data to be specific."
            ),
            sections=("profile", "mission", "health", "roadmap"),
        ),
    )
}

DEFAULT_AGENT = "strategist"
ROUTER_VERSION = "orchestrator.intent@v1"
