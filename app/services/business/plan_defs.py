from dataclasses import dataclass


@dataclass(frozen=True)
class PlanSection:
    key: str
    heading: str
    guidance: str


PLAN_SECTIONS: tuple[PlanSection, ...] = (
    PlanSection(
        "executive_summary",
        "Executive Summary",
        "A concise overview of the business, the opportunity, and why it will win.",
    ),
    PlanSection(
        "problem",
        "Problem & Opportunity",
        "The customer problem, its size and urgency, and the market opportunity.",
    ),
    PlanSection(
        "solution",
        "Solution & Product",
        "What the product is, how it solves the problem, and its current state.",
    ),
    PlanSection(
        "market",
        "Market & Customers",
        "Target customers/segments, market size, and demand evidence.",
    ),
    PlanSection(
        "business_model",
        "Business Model",
        "How the business makes money: revenue streams, pricing, unit economics.",
    ),
    PlanSection(
        "gtm",
        "Go-to-Market",
        "How the business reaches and acquires customers; channels and motion.",
    ),
    PlanSection(
        "competition",
        "Competition",
        "Key competitors, alternatives, and this startup's differentiation.",
    ),
    PlanSection(
        "team",
        "Team",
        "The team, relevant strengths, and gaps to fill.",
    ),
    PlanSection(
        "financials",
        "Financials & Projections",
        "High-level projections, key assumptions, and funding needs.",
    ),
    PlanSection(
        "milestones",
        "Roadmap & Milestones",
        "Near-term milestones and the path to the next stage.",
    ),
)


SECTIONS_BY_KEY = {s.key: s for s in PLAN_SECTIONS}

# PRD 08.11 job screen stage captions, shown while the section is being written.
STAGE_CAPTIONS = {
    "executive_summary": "Compiling your canvases",
    "problem": "Framing the problem and opportunity",
    "solution": "Describing your solution",
    "market": "Sizing your market",
    "business_model": "Explaining how you make money",
    "gtm": "Mapping your go-to-market",
    "competition": "Positioning you against competitors",
    "team": "Presenting your team",
    "financials": "Writing financial narrative",
    "milestones": "Laying out your milestones",
}

AUDIENCES = ("bank", "investors", "internal")
LENGTHS = ("concise", "full")
DEFAULT_OPTIONS = {"audience": "investors", "length": "concise"}
