import uuid
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus, JobStatus, MembershipRole
from app.db.models.job import Job
from app.platform.events import event_bus
from tests.factories import create_assessment, create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _team_member(db, startup):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=MembershipRole.team_member)
    db.flush()
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _walk(client, h, aid):
    # Answer questions until none remain, using deterministic values.
    values = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    while True:
        nq = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h).json()["data"][
            "next_question"
        ]
        if nq is None:
            break
        if nq["qtype"] == "single_choice":
            val = nq["options"][0]["value"]
        elif nq["qtype"] == "multi_choice":
            val = [nq["options"][0]["value"]]
        else:
            val = values[nq["qtype"]]
        r = client.post(
            f"/api/v1/assessments/{aid}/answers",
            headers=h,
            json={"question_key": nq["key"], "value": val},
        )
        assert r.status_code == 200, r.text


def test_complete_gate_blocks_incomplete(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ASSESSMENT_INCOMPLETE"
    # names the still-unanswered question so the client knows what to do next
    assert "product_stage" in r.json()["error"]["message"]


def test_complete_scores_and_sideeffects(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)

    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "completed"
    assert set(data["dimension_scores"]) == {"product", "market", "money", "legal", "team"}
    assert data["narrative"]
    assert db.query(AssessmentResult).filter_by(assessment_id=aid).count() == 1

    db.refresh(s)
    db.refresh(s.profile)
    assert s.profile.assessment_pending is False  # initial type flips it

    jobs = db.query(Job).filter(Job.status == JobStatus.queued).all()
    types = {j.type for j in jobs}
    assert {"healthscore.recalculate", "roadmap.replan"} <= types
    for j in jobs:
        if j.type in {"healthscore.recalculate", "roadmap.replan"}:
            assert j.payload["startup_id"] == str(s.id)
            assert j.payload["assessment_id"] == aid

    assert event_bus.published[-1][0] == "assessment.completed"
    assert event_bus.published[-1][1]["assessment_id"] == aid
    assert event_bus.published[-1][1]["startup_id"] == str(s.id)


def test_complete_quarterly_does_not_touch_assessment_pending(client, db):
    u, s, h = _founder(db)
    create_assessment(db, s, creator=u, status=AssessmentStatus.completed)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    assert client.post("/api/v1/assessments", headers=h).json()["data"]["type"] == "quarterly"
    _walk(client, h, aid)

    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)

    assert r.status_code == 200, r.text
    db.refresh(s)
    db.refresh(s.profile)
    # quarterly re-assessment must not re-flip a founder's already-cleared pending flag
    assert s.profile.assessment_pending is True


def test_complete_is_idempotent_no_reenqueue(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)

    first = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    assert first.status_code == 200, first.text
    before_jobs = db.query(Job).count()
    before_events = len(event_bus.published)

    second = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)

    assert second.status_code == 200, second.text
    assert second.json()["data"] == first.json()["data"]
    assert db.query(Job).count() == before_jobs  # no new jobs
    assert len(event_bus.published) == before_events  # no re-fired event
    assert db.query(AssessmentResult).filter_by(assessment_id=aid).count() == 1  # not re-scored


def test_complete_requires_founder(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)

    member_h = _team_member(db, s)
    db.commit()
    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=member_h)
    assert r.status_code == 403


def test_complete_cross_tenant_assessment_404(client, db):
    _u_b, s_b, h_b = _founder(db)
    b_assessment = create_assessment(db, s_b)
    db.commit()

    _u_a, _s_a, h_a = _founder(db)
    db.commit()

    r = client.post(f"/api/v1/assessments/{b_assessment.id}/complete", headers=h_a)
    assert r.status_code == 404


def test_complete_unknown_assessment_404(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.post(f"/api/v1/assessments/{uuid.uuid4()}/complete", headers=h)
    assert r.status_code == 404
