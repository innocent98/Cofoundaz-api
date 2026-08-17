import pytest

from app.core.errors import AppError
from app.db.models.enums import BusinessModel, Dimension, StartupStage
from app.services.assessment.bank import ASSESSMENT_BANK, QType, Question, question_by_key
from app.services.assessment.engine import is_applicable, next_question, validate_answer
from tests.factories import create_startup, create_user


def _startup(db, **kw):
    u = create_user(db)
    return create_startup(db, owner=u, **kw)


def test_show_if_prior_answer(db):
    s = _startup(db)
    mrr = question_by_key(ASSESSMENT_BANK, "mrr")
    assert is_applicable(mrr, {"has_revenue": "yes"}, s) is True
    assert is_applicable(mrr, {"has_revenue": "no"}, s) is False
    assert is_applicable(mrr, {}, s) is False  # unanswered gate → not applicable yet


def test_next_question_skips_inapplicable(db):
    s = _startup(db)
    # First question is always product_stage (no show_if)
    q = next_question(ASSESSMENT_BANK, {}, s)
    assert q.key == "product_stage"
    # Answering has_revenue=no means mrr is skipped; next after it moves on
    answers = {
        "product_stage": "idea",
        "market_clarity": 3,
        "market_research": "some",
        "has_revenue": "no",
    }
    q2 = next_question(ASSESSMENT_BANK, answers, s)
    assert q2.key == "runway_confidence"  # mrr skipped, next applicable is runway_confidence


def test_next_question_none_when_done(db):
    s = _startup(db)
    # Answer every applicable question (has_revenue=no, incorporated=no prune the conditionals)
    answers = {
        "product_stage": "idea",
        "market_clarity": 3,
        "market_research": "some",
        "has_revenue": "no",
        "runway_confidence": 3,
        "incorporated": "no",
        "team_size": "solo",
        "team_confidence": 4,
    }
    assert next_question(ASSESSMENT_BANK, answers, s) is None


def test_validate_answer_types():
    validate_answer(question_by_key(ASSESSMENT_BANK, "product_stage"), "idea")  # ok
    with pytest.raises(AppError):
        validate_answer(question_by_key(ASSESSMENT_BANK, "product_stage"), "bogus")
    validate_answer(question_by_key(ASSESSMENT_BANK, "market_clarity"), 5)  # ok
    with pytest.raises(AppError):
        validate_answer(question_by_key(ASSESSMENT_BANK, "market_clarity"), 9)  # scale out of range


# --- Extra coverage: v1 bank questions only use `answer` show_if conditions, so exercise
# _cond's `field`/`all`/`any` branches (via the public is_applicable surface) with synthetic
# show_if dicts on minimal questions built inline. ---

_FIELD_STAGE_Q = Question(
    "growth_specific",
    Dimension.market,
    "Market",
    QType.SHORT_TEXT,
    {"max": 0},
    show_if={"field": "stage", "in": ["growth", "scale"]},
)

_FIELD_MODEL_Q = Question(
    "b2b_specific",
    Dimension.market,
    "Market",
    QType.SHORT_TEXT,
    {"max": 0},
    show_if={"field": "business_model", "eq": "b2b"},
)

_ALL_Q = Question(
    "all_gated",
    Dimension.market,
    "Market",
    QType.SHORT_TEXT,
    {"max": 0},
    show_if={
        "all": [
            {"field": "stage", "eq": "growth"},
            {"answer": "has_revenue", "eq": "yes"},
        ]
    },
)

_ANY_Q = Question(
    "any_gated",
    Dimension.market,
    "Market",
    QType.SHORT_TEXT,
    {"max": 0},
    show_if={
        "any": [
            {"field": "stage", "eq": "idea"},
            {"answer": "has_revenue", "eq": "yes"},
        ]
    },
)


def test_show_if_field_in(db):
    growth = _startup(db, stage=StartupStage.growth)
    idea = _startup(db, stage=StartupStage.idea)
    unset = _startup(db)
    assert is_applicable(_FIELD_STAGE_Q, {}, growth) is True
    assert is_applicable(_FIELD_STAGE_Q, {}, idea) is False
    assert is_applicable(_FIELD_STAGE_Q, {}, unset) is False


def test_show_if_field_eq_unwraps_enum_value(db):
    b2b = _startup(db, business_model=BusinessModel.b2b)
    b2c = _startup(db, business_model=BusinessModel.b2c)
    assert is_applicable(_FIELD_MODEL_Q, {}, b2b) is True
    assert is_applicable(_FIELD_MODEL_Q, {}, b2c) is False


def test_show_if_all_requires_every_condition(db):
    s = _startup(db, stage=StartupStage.growth)
    assert is_applicable(_ALL_Q, {"has_revenue": "yes"}, s) is True
    assert is_applicable(_ALL_Q, {"has_revenue": "no"}, s) is False


def test_show_if_any_requires_one_condition(db):
    s = _startup(db, stage=StartupStage.growth)  # field cond ("idea") is False
    assert is_applicable(_ANY_Q, {"has_revenue": "yes"}, s) is True
    assert is_applicable(_ANY_Q, {"has_revenue": "no"}, s) is False


# --- Extra coverage: the v1 bank has no multi_choice/short_text question, so exercise
# validate_answer's remaining branches with synthetic questions built inline. ---

_MULTI = Question(
    "favorite_channels",
    Dimension.market,
    "Market",
    QType.MULTI_CHOICE,
    {"max": 3},
    options=[
        {"value": "seo", "label": "SEO"},
        {"value": "ads", "label": "Ads"},
        {"value": "referral", "label": "Referral"},
    ],
)

_CURRENCY = Question(
    "runway_cash",
    Dimension.money,
    "Money",
    QType.NUMERIC_CURRENCY,
    {"max": 0},
)

_TEXT = Question(
    "one_liner",
    Dimension.product,
    "Product",
    QType.SHORT_TEXT,
    {"max": 0},
)


def test_validate_answer_multi_choice():
    validate_answer(_MULTI, ["seo", "ads"])  # ok
    with pytest.raises(AppError):
        validate_answer(_MULTI, [])  # empty list
    with pytest.raises(AppError):
        validate_answer(_MULTI, "seo")  # not a list
    with pytest.raises(AppError):
        validate_answer(_MULTI, ["seo", "bogus"])  # unknown option


def test_validate_answer_numeric_currency():
    validate_answer(_CURRENCY, 0)  # ok
    validate_answer(_CURRENCY, 1500.50)  # ok
    with pytest.raises(AppError):
        validate_answer(_CURRENCY, -1)  # negative
    with pytest.raises(AppError):
        validate_answer(_CURRENCY, True)  # bool must not pass as numeric


def test_validate_answer_short_text():
    validate_answer(_TEXT, "We help founders ship faster.")  # ok
    with pytest.raises(AppError):
        validate_answer(_TEXT, "")  # empty
    with pytest.raises(AppError):
        validate_answer(_TEXT, "   ")  # whitespace only
