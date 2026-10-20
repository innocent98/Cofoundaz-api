"""D-ASM-2: progress = answered / (answered + remaining applicable), integer 0-100."""

from types import SimpleNamespace

from app.db.models.enums import Dimension
from app.services.assessment.bank import ASSESSMENT_BANK, Bank, QType, Question
from app.services.assessment.engine import progress

STARTUP = SimpleNamespace(stage=None)


def test_empty_answers_is_zero_of_eight_unconditional_questions():
    assert progress(ASSESSMENT_BANK, {}, STARTUP) == {"answered": 0, "remaining": 8, "pct": 0}


def test_gated_followup_widens_the_denominator():
    p = progress(ASSESSMENT_BANK, {"product_stage": "live"}, STARTUP)
    assert p == {"answered": 1, "remaining": 8, "pct": 11}
    p = progress(ASSESSMENT_BANK, {"product_stage": "idea"}, STARTUP)
    assert p == {"answered": 1, "remaining": 7, "pct": 12}


def test_pct_floors_and_full_is_100():
    bank = Bank(
        "t",
        [Question(f"q{i}", Dimension.product, "P", QType.SCALE_1_5, {"max": 5}) for i in range(3)],
    )
    assert progress(bank, {"q0": 1}, STARTUP)["pct"] == 33
    assert progress(bank, {"q0": 1, "q1": 1, "q2": 1}, STARTUP) == {
        "answered": 3,
        "remaining": 0,
        "pct": 100,
    }


def test_empty_bank_is_100_not_a_division_error():
    assert progress(Bank("t", []), {}, STARTUP) == {"answered": 0, "remaining": 0, "pct": 100}


def test_answers_to_retired_or_now_hidden_questions_do_not_count():
    answers = {"product_stage": "idea", "product_confidence": 3, "retired_key": "x"}
    # product_confidence is hidden on 'idea'; retired_key is not in the bank
    assert progress(ASSESSMENT_BANK, answers, STARTUP) == {"answered": 1, "remaining": 7, "pct": 12}
