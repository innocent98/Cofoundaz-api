"""Assessment PRD list/next-question additions: progress (D-ASM-2), status filter, stage_at_time
(D-ASM-3) and the quarterly_due flag."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.assessment import Assessment
from app.db.models.enums import AssessmentStatus, AssessmentType, StartupStage
from app.db.models.startup import Startup
from tests.api.assessment.helpers import BASE, founder, take_and_complete, walk
from tests.factories import create_assessment


def _answer(client, h, aid, key, value):
    r = client.post(f"{BASE}/{aid}/answers", headers=h, json={"question_key": key, "value": value})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------- progress ----------


def test_progress_on_start_next_question_and_answers(client, db):
    _u, _s, h = founder(db)
    db.commit()
    start = client.post(BASE, headers=h).json()["data"]
    aid = start["assessment_id"]
    assert start["progress"] == {"answered": 0, "remaining": 8, "pct": 0}

    nq = client.get(f"{BASE}/{aid}/next-question", headers=h).json()["data"]
    assert nq["progress"] == {"answered": 0, "remaining": 8, "pct": 0}

    after = _answer(client, h, aid, "product_stage", "idea")
    assert after["progress"] == {"answered": 1, "remaining": 7, "pct": 12}


def test_progress_moves_when_an_adaptive_answer_opens_more_questions(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid = client.post(BASE, headers=h).json()["data"]["assessment_id"]
    # 'live' opens the product_confidence follow-up: 1 answered, 8 remaining -> 11%, not 12%.
    after = _answer(client, h, aid, "product_stage", "live")
    assert after["progress"] == {"answered": 1, "remaining": 8, "pct": 11}


def test_progress_reaches_100_when_walked_and_is_null_on_completed_list_rows(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid = client.post(BASE, headers=h).json()["data"]["assessment_id"]
    walk(client, h, aid)
    nq = client.get(f"{BASE}/{aid}/next-question", headers=h).json()["data"]
    assert nq["next_question"] is None
    assert nq["progress"]["remaining"] == 0 and nq["progress"]["pct"] == 100

    in_progress_row = client.get(BASE, headers=h).json()["data"][0]
    assert in_progress_row["progress"]["pct"] == 100

    client.post(f"{BASE}/{aid}/complete", headers=h)
    done_row = client.get(BASE, headers=h).json()["data"][0]
    assert done_row["status"] == "completed"
    assert done_row["progress"] is None


def test_list_row_progress_matches_answers_so_far(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid = client.post(BASE, headers=h).json()["data"]["assessment_id"]
    _answer(client, h, aid, "product_stage", "idea")
    row = client.get(BASE, headers=h).json()["data"][0]
    assert row["id"] == aid
    assert row["progress"] == {"answered": 1, "remaining": 7, "pct": 12}


# ---------- status filter ----------


def test_status_filter(client, db):
    _u, _s, h = founder(db)
    db.commit()
    done = take_and_complete(client, h)
    open_ = client.post(BASE, headers=h).json()["data"]["assessment_id"]

    everything = {r["id"] for r in client.get(BASE, headers=h).json()["data"]}
    assert everything == {done, open_}
    only_open = client.get(f"{BASE}?status=in_progress", headers=h).json()["data"]
    assert [r["id"] for r in only_open] == [open_]
    only_done = client.get(f"{BASE}?status=completed", headers=h).json()["data"]
    assert [r["id"] for r in only_done] == [done]


@pytest.mark.parametrize("bad", ["done", "COMPLETED", "", "in-progress"])
def test_invalid_status_is_422(client, db, bad):
    _u, _s, h = founder(db)
    db.commit()
    r = client.get(f"{BASE}?status={bad}", headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------- stage_at_time ----------


def test_stage_snapshot_taken_at_completion_and_not_rewritten(client, db):
    _u, s, h = founder(db, stage=StartupStage.build)
    db.commit()
    aid = take_and_complete(client, h)

    s_row = db.get(Startup, s.id)
    s_row.stage = StartupStage.growth
    db.commit()

    listed = client.get(BASE, headers=h).json()["data"][0]
    assert listed["stage_at_time"] == "build"
    detail = client.get(f"{BASE}/{aid}", headers=h).json()["data"]
    assert detail["stage_at_time"] == "build"


def test_stage_is_null_for_in_progress_and_for_rows_that_predate_the_snapshot(client, db):
    _u, s, h = founder(db, stage=StartupStage.launch)
    old = create_assessment(db, s, status=AssessmentStatus.completed)
    old.completed_at = datetime.now(UTC) - timedelta(days=200)
    db.commit()
    aid = client.post(BASE, headers=h).json()["data"]["assessment_id"]

    rows = {r["id"]: r for r in client.get(BASE, headers=h).json()["data"]}
    assert rows[str(old.id)]["stage_at_time"] is None
    assert rows[aid]["stage_at_time"] is None
    assert client.get(f"{BASE}/{old.id}", headers=h).json()["data"]["stage_at_time"] is None


def test_stage_snapshot_is_null_when_startup_has_no_stage(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid = take_and_complete(client, h)
    assert client.get(f"{BASE}/{aid}", headers=h).json()["data"]["stage_at_time"] is None


# ---------- quarterly_due ----------


def _quarterly_due(client, h) -> bool:
    body = client.get(BASE, headers=h).json()
    return body["meta"]["quarterly_due"]


def test_quarterly_due_false_with_no_assessment_and_when_freshly_completed(client, db):
    _u, _s, h = founder(db)
    db.commit()
    assert _quarterly_due(client, h) is False
    take_and_complete(client, h)
    assert _quarterly_due(client, h) is False


def test_quarterly_due_true_after_90_days_same_rule_as_the_cron(client, db):
    _u, s, h = founder(db)
    db.commit()
    aid = take_and_complete(client, h)
    a = db.get(Assessment, uuid.UUID(aid))
    a.completed_at = datetime.now(UTC) - timedelta(days=91)
    db.commit()
    assert _quarterly_due(client, h) is True

    a.completed_at = datetime.now(UTC) - timedelta(days=89)
    db.commit()
    assert _quarterly_due(client, h) is False


def test_quarterly_due_false_while_a_reassessment_is_in_progress(client, db):
    _u, s, h = founder(db)
    db.commit()
    aid = take_and_complete(client, h)
    db.get(Assessment, uuid.UUID(aid)).completed_at = datetime.now(UTC) - timedelta(days=120)
    db.commit()
    assert _quarterly_due(client, h) is True
    client.post(BASE, headers=h)  # starts the quarterly re-assessment
    assert _quarterly_due(client, h) is False


def test_quarterly_due_agrees_with_the_scheduler(client, db):
    from app.worker.scheduler import _due_quarterly

    _u, s, h = founder(db)
    db.commit()
    aid = take_and_complete(client, h)
    a = db.get(Assessment, uuid.UUID(aid))
    a.completed_at = datetime.now(UTC) - timedelta(days=95)
    db.commit()
    now = datetime.now(UTC)
    assert any(d.startup_id == s.id for d in _due_quarterly(db, now)) is _quarterly_due(client, h)
    a.completed_at = datetime.now(UTC) - timedelta(days=10)
    db.commit()
    assert any(d.startup_id == s.id for d in _due_quarterly(db, now)) is _quarterly_due(client, h)


def test_list_data_stays_a_list_and_meta_keeps_envelope_keys(client, db):
    _u, _s, h = founder(db)
    db.commit()
    body = client.get(BASE, headers=h).json()
    assert body["data"] == []
    assert set(body["meta"]) == {"next_cursor", "total_estimate", "quarterly_due"}


def test_type_is_quarterly_for_second_assessment(client, db):
    _u, _s, h = founder(db)
    db.commit()
    take_and_complete(client, h)
    client.post(BASE, headers=h)
    types = {r["type"] for r in client.get(BASE, headers=h).json()["data"]}
    assert types == {AssessmentType.initial.value, AssessmentType.quarterly.value}
