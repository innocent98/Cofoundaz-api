"""Row 11: GET /assessments/{id} gains a structured `answers` list (prompt, human label,
changed_from vs the previous COMPLETED assessment). `answers_by_dimension` is unchanged."""

from app.db.models.enums import Dimension
from app.services.assessment.bank import QType, Question
from tests.api.assessment.helpers import BASE, founder, take_and_complete, walk
from tests.factories import create_answer, create_assessment


def _flat(detail):
    return {q["question_key"]: q for g in detail["answers"] for q in g["questions"]}


def test_answers_grouped_by_dimension_with_prompts_and_labels(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid = take_and_complete(client, h, pick=0)
    d = client.get(f"{BASE}/{aid}", headers=h).json()["data"]

    assert [g["dimension"] for g in d["answers"]] == [x.value for x in Dimension]
    q = _flat(d)
    assert q["product_stage"]["prompt"] == "How far along is your product?"
    assert q["product_stage"]["qtype"] == "single_choice"
    assert q["product_stage"]["value"] == "idea"
    assert q["product_stage"]["answer_label"] == "Just an idea"
    assert q["market_clarity"]["qtype"] == "scale_1_5"
    assert q["market_clarity"]["answer_label"] == "3"
    # every answered question is present exactly once, and the raw map is untouched
    raw = {e["question_key"] for b in d["answers_by_dimension"].values() for e in b}
    assert set(q) == raw
    for e in q.values():
        assert set(e) == {
            "question_key",
            "prompt",
            "qtype",
            "value",
            "answer_label",
            "changed",
            "changed_from",
        }


def test_no_previous_assessment_means_nothing_changed(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid = take_and_complete(client, h)
    d = client.get(f"{BASE}/{aid}", headers=h).json()["data"]
    assert all(q["changed"] is False and q["changed_from"] is None for q in _flat(d).values())


def test_changed_from_is_the_previous_completed_assessments_label(client, db):
    _u, _s, h = founder(db)
    db.commit()
    take_and_complete(client, h, pick=0)  # product_stage idea
    second = take_and_complete(client, h, pick=-1)  # product_stage live
    d = client.get(f"{BASE}/{second}", headers=h).json()["data"]
    q = _flat(d)

    assert q["product_stage"]["answer_label"] == "Live with users"
    assert q["product_stage"]["changed"] is True
    assert q["product_stage"]["changed_from"] == "Just an idea"
    # same answer both times -> not changed, changed_from null
    assert q["market_clarity"]["changed"] is False
    assert q["market_clarity"]["changed_from"] is None
    # asked now but not asked last time (adaptive follow-up) -> null, not changed
    assert "product_confidence" in q
    assert q["product_confidence"]["changed"] is False
    assert q["product_confidence"]["changed_from"] is None


def test_previous_is_the_latest_completed_before_this_one_not_an_in_progress_one(client, db):
    _u, _s, h = founder(db)
    db.commit()
    first = take_and_complete(client, h, pick=0)
    second = take_and_complete(client, h, pick=-1)
    # the first assessment has no earlier completed assessment
    d1 = client.get(f"{BASE}/{first}", headers=h).json()["data"]
    assert _flat(d1)["product_stage"]["changed"] is False

    # an in-progress third compares against the latest completed (second), never itself
    third = client.post(BASE, headers=h).json()["data"]["assessment_id"]
    walk(client, h, third, pick=-1)
    d3 = client.get(f"{BASE}/{third}", headers=h).json()["data"]
    q3 = _flat(d3)
    assert q3["product_stage"]["changed"] is False  # live vs live
    assert d3["status"] == "in_progress"
    assert d3["result"] is None
    assert second != third


def test_numeric_label_is_the_plain_number(client, db):
    _u, s, h = founder(db)
    a = create_assessment(db, s)
    create_answer(db, a, question_key="mrr", value=2500)
    db.commit()
    d = client.get(f"{BASE}/{a.id}", headers=h).json()["data"]
    assert _flat(d)["mrr"]["answer_label"] == "2500"


def test_answer_label_covers_every_qtype():
    from app.services.assessment.service import answer_label

    opts = [{"value": "a", "label": "Alpha"}, {"value": "b", "label": "Beta"}]

    def q(qtype):
        return Question("k", Dimension.product, "P", qtype, {}, options=opts)

    assert answer_label(q(QType.SINGLE_CHOICE), "b") == "Beta"
    assert answer_label(q(QType.MULTI_CHOICE), ["a", "b"]) == "Alpha, Beta"
    assert answer_label(q(QType.SCALE_1_5), 4) == "4"
    assert answer_label(q(QType.NUMERIC_CURRENCY), 1500.0) == "1500"
    assert answer_label(q(QType.NUMERIC_CURRENCY), 12.5) == "12.5"
    assert answer_label(q(QType.SHORT_TEXT), "hi") == "hi"
    assert answer_label(q(QType.SINGLE_CHOICE), "retired_option") == "retired_option"
    assert answer_label(None, {"x": 1}) == "{'x': 1}"


def test_unknown_question_key_is_kept_with_null_prompt(client, db):
    _u, s, h = founder(db)
    a = create_assessment(db, s)
    create_answer(db, a, question_key="retired_question_from_v0", value="n/a")
    db.commit()
    d = client.get(f"{BASE}/{a.id}", headers=h).json()["data"]
    last = d["answers"][-1]
    assert last["dimension"] == "unknown"
    assert last["questions"] == [
        {
            "question_key": "retired_question_from_v0",
            "prompt": None,
            "qtype": None,
            "value": "n/a",
            "answer_label": "n/a",
            "changed": False,
            "changed_from": None,
        }
    ]
