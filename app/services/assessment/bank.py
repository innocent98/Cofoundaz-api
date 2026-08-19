from dataclasses import dataclass

from app.db.models.enums import Dimension


class QType:
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SCALE_1_5 = "scale_1_5"
    NUMERIC_CURRENCY = "numeric_currency"
    SHORT_TEXT = "short_text"


@dataclass(frozen=True)
class Question:
    key: str
    dimension: Dimension
    section: str
    qtype: str
    scoring: dict
    options: list[dict] | None = None
    show_if: dict | None = None


@dataclass(frozen=True)
class Bank:
    version: str
    questions: list[Question]


def question_by_key(bank: Bank, key: str) -> Question | None:
    return next((q for q in bank.questions if q.key == key), None)


def _opt(value: str, label: str) -> dict:
    return {"value": value, "label": label}


ASSESSMENT_BANK = Bank(
    version="v1",
    questions=[
        # --- Product ---
        Question(
            "product_stage",
            Dimension.product,
            "Product",
            QType.SINGLE_CHOICE,
            {"max": 4, "idea": 1, "prototype": 2, "mvp": 3, "live": 4},
            options=[
                _opt("idea", "Just an idea"),
                _opt("prototype", "A prototype"),
                _opt("mvp", "A working MVP"),
                _opt("live", "Live with users"),
            ],
        ),
        Question(
            "product_confidence",
            Dimension.product,
            "Product",
            QType.SCALE_1_5,
            {"max": 5},
            show_if={"answer": "product_stage", "in": ["mvp", "live"]},
        ),
        # --- Market ---
        Question("market_clarity", Dimension.market, "Market", QType.SCALE_1_5, {"max": 5}),
        Question(
            "market_research",
            Dimension.market,
            "Market",
            QType.SINGLE_CHOICE,
            {"max": 3, "none": 0, "some": 2, "deep": 3},
            options=[
                _opt("none", "No research yet"),
                _opt("some", "Some interviews"),
                _opt("deep", "Deep, ongoing research"),
            ],
        ),
        # --- Money ---
        Question(
            "has_revenue",
            Dimension.money,
            "Money",
            QType.SINGLE_CHOICE,
            {"max": 2, "no": 0, "yes": 2},
            options=[_opt("no", "Not yet"), _opt("yes", "Yes, we have revenue")],
        ),
        Question(
            "mrr",
            Dimension.money,
            "Money",
            QType.NUMERIC_CURRENCY,
            {"max": 0},
            show_if={"answer": "has_revenue", "eq": "yes"},
        ),
        Question("runway_confidence", Dimension.money, "Money", QType.SCALE_1_5, {"max": 5}),
        # --- Legal ---
        Question(
            "incorporated",
            Dimension.legal,
            "Legal",
            QType.SINGLE_CHOICE,
            {"max": 2, "no": 0, "yes": 2},
            options=[_opt("no", "Not incorporated"), _opt("yes", "Incorporated")],
        ),
        Question(
            "ip_assigned",
            Dimension.legal,
            "Legal",
            QType.SINGLE_CHOICE,
            {"max": 2, "no": 0, "unsure": 1, "yes": 2},
            options=[_opt("no", "No"), _opt("unsure", "Not sure"), _opt("yes", "Yes")],
            show_if={"answer": "incorporated", "eq": "yes"},
        ),
        # --- Team ---
        Question(
            "team_size",
            Dimension.team,
            "Team",
            QType.SINGLE_CHOICE,
            {"max": 3, "solo": 1, "cofounders": 3, "team": 3},
            options=[
                _opt("solo", "Just me"),
                _opt("cofounders", "Co-founders"),
                _opt("team", "A team"),
            ],
        ),
        Question("team_confidence", Dimension.team, "Team", QType.SCALE_1_5, {"max": 5}),
    ],
)
