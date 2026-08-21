from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import JobStatus, UserStatus
from app.db.models.job import Job
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def _fill(client, h):
    client.get("/api/v1/onboarding/state", headers=h)
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 1, "full_name": "Ada"})
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
    client.patch(
        "/api/v1/onboarding/state",
        headers=h,
        json={"step": 3, "industry": "Fintech", "stage": "idea"},
    )
    client.patch(
        "/api/v1/onboarding/state", headers=h, json={"step": 4, "goals": ["Get first customers"]}
    )


def test_complete_gate_blocks_incomplete(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)  # nothing filled
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ONBOARDING_INCOMPLETE"
    assert r.json()["error"]["field_errors"]


def test_complete_enqueues_two_jobs(client, db):
    u, h = _auth(db)
    db.commit()
    _fill(client, h)
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["job_ids"]) == 2 and data["assessment_pending"] is True
    statuses = {j.type: j.status for j in db.query(Job).all()}
    # roadmap.generate now runs inline during onboarding-complete, so its job
    # is recorded as already succeeded; healthscore.initialize is still an
    # unconsumed queued stub (Health Score is pending until the assessment).
    assert statuses["roadmap.generate"] == JobStatus.succeeded
    assert statuses["healthscore.initialize"] == JobStatus.queued


def test_complete_is_idempotent(client, db):
    u, h = _auth(db)
    db.commit()
    _fill(client, h)
    client.post("/api/v1/onboarding/complete", headers=h)
    before = db.query(Job).count()
    r = client.post("/api/v1/onboarding/complete", headers=h)  # again
    assert r.status_code == 200 and r.json()["data"]["completed"] is True
    assert db.query(Job).count() == before  # no new jobs
