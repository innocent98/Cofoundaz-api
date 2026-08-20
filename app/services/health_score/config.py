HEALTH_CONFIG_VERSION = 1

DIMENSION_WEIGHTS: dict[str, float] = {
    "product": 0.20,
    "market": 0.20,
    "money": 0.20,
    "legal": 0.20,
    "team": 0.20,
}

# (low_inclusive, high_inclusive, band_key)
BANDS: list[tuple[int, int, str]] = [
    (0, 39, "at_risk"),
    (40, 59, "needs_work"),
    (60, 79, "healthy"),
    (80, 100, "thriving"),
]

MIN_COHORT_SIZE = 5

DIMENSION_LABELS: dict[str, str] = {
    "product": "Product",
    "market": "Market",
    "money": "Financial",
    "legal": "Legal",
    "team": "Team",
}

RECOMMENDATION_CATALOG: dict[str, list[dict]] = {
    "product": [
        {
            "key": "product.define_mvp",
            "title": "Define your MVP scope",
            "body": "Write a one-page MVP definition: the single problem, the smallest feature set that solves it, and what you are deliberately leaving out.",
            "estimated_lift": 8,
            "effort": "medium",
            "triggers_below": 60,
        },
        {
            "key": "product.user_feedback_loop",
            "title": "Set up a user feedback loop",
            "body": "Put a lightweight channel in front of real users (calls, a form, a Slack) and commit to reviewing it weekly.",
            "estimated_lift": 6,
            "effort": "low",
            "triggers_below": 50,
        },
    ],
    "market": [
        {
            "key": "market.icp_definition",
            "title": "Write a one-page ICP",
            "body": "Define your ideal customer profile: who they are, the pain, and why now. Specificity beats reach at this stage.",
            "estimated_lift": 7,
            "effort": "low",
            "triggers_below": 60,
        },
        {
            "key": "market.competitor_map",
            "title": "Map your top 5 competitors",
            "body": "List the five closest alternatives (including 'do nothing') and one sentence on how you differ from each.",
            "estimated_lift": 5,
            "effort": "low",
            "triggers_below": 50,
        },
    ],
    "money": [
        {
            "key": "money.runway_model",
            "title": "Build a 12-month runway model",
            "body": "Model monthly cash in/out for 12 months so you know your runway and the month you must raise or break even.",
            "estimated_lift": 8,
            "effort": "medium",
            "triggers_below": 60,
        },
        {
            "key": "money.pricing_experiment",
            "title": "Run a pricing experiment",
            "body": "Test one concrete price point with real prospects. Willingness-to-pay evidence de-risks your whole model.",
            "estimated_lift": 6,
            "effort": "medium",
            "triggers_below": 50,
        },
    ],
    "legal": [
        {
            "key": "legal.incorporate",
            "title": "Complete incorporation",
            "body": "Register the company and issue founder shares. Operating unincorporated exposes founders personally and blocks fundraising.",
            "estimated_lift": 9,
            "effort": "high",
            "triggers_below": 60,
        },
        {
            "key": "legal.founder_agreement",
            "title": "Sign a founders' agreement",
            "body": "Put equity splits, vesting, and roles in writing before it is contentious. This prevents the most common founder disputes.",
            "estimated_lift": 7,
            "effort": "medium",
            "triggers_below": 50,
        },
    ],
    "team": [
        {
            "key": "team.roles_clarity",
            "title": "Clarify founder roles & equity",
            "body": "Write down who owns what decisions and the equity split with vesting. Ambiguity here compounds fast.",
            "estimated_lift": 7,
            "effort": "medium",
            "triggers_below": 60,
        },
        {
            "key": "team.hiring_plan",
            "title": "Draft a 6-month hiring plan",
            "body": "List the next 2–3 critical hires, when, and why. A plan turns hiring from reactive to intentional.",
            "estimated_lift": 5,
            "effort": "low",
            "triggers_below": 50,
        },
    ],
}
