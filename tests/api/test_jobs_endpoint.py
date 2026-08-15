import uuid

from app.platform.jobs import JobDispatcher


def test_get_job_returns_envelope(client, db):
    job = JobDispatcher().enqueue(db, "healthscore.initialize", {})
    db.commit()
    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "queued"
    assert body["data"]["type"] == "healthscore.initialize"


def test_get_missing_job_404(client):
    resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
