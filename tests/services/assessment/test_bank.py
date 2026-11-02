from app.db.models.enums import Dimension
from app.services.assessment.bank import ASSESSMENT_BANK, QType, question_by_key


def test_bank_versioned_and_nonempty():
    assert ASSESSMENT_BANK.version == "v1"
    assert len(ASSESSMENT_BANK.questions) >= 8


def test_every_dimension_has_a_scored_question():
    for dim in Dimension:
        scored = [
            q
            for q in ASSESSMENT_BANK.questions
            if q.dimension == dim and q.scoring.get("max", 0) > 0
        ]
        assert scored, f"dimension {dim} has no scored question"


def test_question_keys_unique_and_lookup():
    keys = [q.key for q in ASSESSMENT_BANK.questions]
    assert len(keys) == len(set(keys))
    assert question_by_key(ASSESSMENT_BANK, keys[0]).key == keys[0]
    assert question_by_key(ASSESSMENT_BANK, "nope") is None


def test_choice_questions_have_options_and_scoring_within_max():
    for q in ASSESSMENT_BANK.questions:
        if q.qtype in (QType.SINGLE_CHOICE, QType.MULTI_CHOICE):
            assert q.options, q.key
            opt_values = {o["value"] for o in q.options}
            for k, _v in q.scoring.items():
                if k != "max":
                    assert k in opt_values, f"{q.key}: scoring key {k} not an option"


def test_every_bank_question_has_prompt():
    """Every real question must carry founder-facing prompt text (the FE renders it).

    `Question.prompt` defaults to "" so synthetic engine-logic fixtures need not set it;
    this guards the actual bank so a new question can't ship promptless (the original
    gap that left the assessment FE with nothing to display).
    """
    missing = [q.key for q in ASSESSMENT_BANK.questions if not (q.prompt and q.prompt.strip())]
    assert not missing, f"bank questions missing prompt text: {missing}"


def test_bank_prompts_are_distinct_and_non_trivial():
    prompts = [q.prompt for q in ASSESSMENT_BANK.questions]
    assert len(set(prompts)) == len(prompts), "duplicate prompts in the bank"
    assert all(len(p) >= 8 for p in prompts), "a bank prompt looks too short to be real"
