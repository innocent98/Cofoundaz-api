from app.db.models.job import JobStatus
from app.platform.jobs import JobDispatcher


def test_enqueue_writes_queued_row(db):
    job = JobDispatcher().enqueue(db, "roadmap.generate", {"startup_id": "s1"})
    db.flush()
    assert job.status == JobStatus.queued
    assert job.type == "roadmap.generate"
    assert job.payload == {"startup_id": "s1"}
