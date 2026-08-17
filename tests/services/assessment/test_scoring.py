from app.db.models.enums import Dimension
from app.services.assessment.bank import ASSESSMENT_BANK, Bank, QType, Question
from app.services.assessment.scoring import score
from tests.factories import create_startup, create_user


def _startup(db, **kw):
    u = create_user(db)
    return create_startup(db, owner=u, **kw)


def test_score_dimensions_0_100(db):
    s = _startup(db)
    answers = {
        "product_stage": "live",
        "product_confidence": 5,
        "market_clarity": 5,
        "market_research": "deep",
        "has_revenue": "yes",
        "mrr": 5000,
        "runway_confidence": 5,
        "incorporated": "yes",
        "ip_assigned": "yes",
        "team_size": "team",
        "team_confidence": 5,
    }
    out = score(ASSESSMENT_BANK, answers, s)
    assert set(out["dimension_scores"]) == {"product", "market", "money", "legal", "team"}
    assert all(0 <= v <= 100 for v in out["dimension_scores"].values())
    assert out["dimension_scores"]["legal"] == 100  # incorporated=yes(2/2)+ip=yes(2/2)
    assert 0 <= out["overall_provisional"] <= 100
    assert isinstance(out["narrative"], str) and out["narrative"]


def test_skipped_questions_excluded_from_denominator(db):
    s = _startup(db)
    # mrr is gated on has_revenue=="yes"; with has_revenue="no" it's inapplicable and must
    # NOT appear in money's denominator (its max is 0 anyway, so this is belt-and-suspenders).
    # has_revenue itself is still a scored, applicable, answered question and DOES count:
    # has_revenue="no" -> 0/2, runway_confidence=1 -> 1/5, so money = round(100*1/7) = 14.
    low = {
        "product_stage": "idea",
        "market_clarity": 1,
        "market_research": "none",
        "has_revenue": "no",
        "runway_confidence": 1,
        "incorporated": "no",
        "team_size": "solo",
        "team_confidence": 1,
    }
    out = score(ASSESSMENT_BANK, low, s)
    assert out["dimension_scores"]["money"] == 14


def test_dimension_with_no_answered_scored_question_defaults_to_50(db):
    s = _startup(db)
    # No money-dimension keys answered at all -> neutral default, not 0.
    answers = {
        "product_stage": "idea",
        "market_clarity": 3,
        "market_research": "some",
        "incorporated": "no",
        "team_size": "solo",
        "team_confidence": 3,
    }
    out = score(ASSESSMENT_BANK, answers, s)
    assert out["dimension_scores"]["money"] == 50


def test_multi_choice_sums_selected_and_caps_at_max():
    # v1 bank has no MULTI_CHOICE question, so exercise that branch directly with a
    # synthetic bank built inline. Pairing the multi_choice question with a second,
    # unrelated scored question in the same dimension keeps the ratio away from 100%
    # so the cap's effect is visible in the number itself, not just masked by saturation.
    channels = Question(
        "growth_channels",
        Dimension.market,
        "Market",
        QType.MULTI_CHOICE,
        {"max": 3, "seo": 2, "ads": 2, "referral": 1},
        options=[
            {"value": "seo", "label": "SEO"},
            {"value": "ads", "label": "Ads"},
            {"value": "referral", "label": "Referral"},
        ],
    )
    clarity = Question("market_clarity", Dimension.market, "Market", QType.SCALE_1_5, {"max": 5})
    bank = Bank(version="test", questions=[channels, clarity])

    class _Startup:
        pass

    startup = _Startup()

    # seo(2) + referral(1) = 3 selected sum, under max=3 -> counts in full.
    # dimension: earned = 3 + 1(clarity) = 4, max = 3 + 5 = 8 -> 50.
    out = score(bank, {"growth_channels": ["seo", "referral"], "market_clarity": 1}, startup)
    assert out["dimension_scores"]["market"] == 50

    # seo(2) + ads(2) = 4 selected sum, capped at max=3.
    # dimension: earned = 3 (capped, not 4) + 1(clarity) = 4, max = 8 -> 50, not 63
    # (63 is what round(100 * 5/8) would give if the cap were NOT applied).
    out_capped = score(bank, {"growth_channels": ["seo", "ads"], "market_clarity": 1}, startup)
    assert out_capped["dimension_scores"]["market"] == 50
