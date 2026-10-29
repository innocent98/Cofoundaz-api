"""The plan catalogue as data. `PLANS` seeds `billing_plans` (migration 0078); the table is the
runtime source, so a price change is a data change plus a new Paystack plan code, not a deploy.

Prices are kobo. Credits are LLM tokens / `TOKENS_PER_CREDIT` (D-SET-9).
"""

from typing import Any

TOKENS_PER_CREDIT = 50

# Feature keys a plan unlocks. `sales_outreach` is the D-SAL-11 abuse gate (paid plans only).
STARTER_MODULES: list[str] = []
GROWTH_MODULES = ["marketing", "sales", "finance", "validation", "analytics", "sales_outreach"]
SCALE_MODULES = [*GROWTH_MODULES, "funding", "investor_readiness", "legal", "priority_support"]

PLANS: list[dict[str, Any]] = [
    {
        "key": "starter",
        "name": "Starter",
        "tagline": "For validating an idea.",
        "rank": 1,
        "price_monthly_minor": 0,
        "price_annual_minor": 0,
        "ai_credits": 500,
        "seats": 1,
        "storage_mb": 5 * 1024,
        "modules": STARTER_MODULES,
        "features": [
            "Core AI Co-Founder",
            "Roadmap and Health Score",
            "Business Builder",
            "Documents",
            "500 AI credits monthly",
            "1 seat",
        ],
    },
    {
        "key": "growth",
        "name": "Growth",
        "tagline": "For getting to revenue.",
        "rank": 2,
        "price_monthly_minor": 24_000_00,
        "price_annual_minor": 240_000_00,
        "ai_credits": 5000,
        "seats": 5,
        "storage_mb": 20 * 1024,
        "modules": GROWTH_MODULES,
        "features": [
            "Everything in Starter",
            "Marketing, Sales, Finance, Validation Hubs",
            "Analytics and Reports",
            "5,000 AI credits monthly",
            "5 seats",
        ],
    },
    {
        "key": "scale",
        "name": "Scale",
        "tagline": "For raising and expanding.",
        "rank": 3,
        "price_monthly_minor": 68_000_00,
        "price_annual_minor": 680_000_00,
        "ai_credits": 20000,
        "seats": None,
        "storage_mb": 100 * 1024,
        "modules": SCALE_MODULES,
        "features": [
            "Everything in Growth",
            "Funding Hub and Investor Readiness",
            "Legal and Compliance",
            "Priority support",
            "20,000 AI credits monthly",
            "Unlimited seats",
        ],
    },
]

# One-time credit packs (D-SET-10). Seats and storage add-ons need recurring billing: later.
ADDONS: list[dict[str, Any]] = [
    {
        "key": "credits_2000",
        "name": "2,000 AI credits",
        "description": "One-time top-up.",
        "credits": 2000,
        "price_minor": 8_000_00,
    },
    {
        "key": "credits_10000",
        "name": "10,000 AI credits",
        "description": "Best value for heavy AI months.",
        "credits": 10000,
        "price_minor": 32_000_00,
    },
]
ADDONS_BY_KEY = {a["key"]: a for a in ADDONS}
