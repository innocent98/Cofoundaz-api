from datetime import UTC, datetime, timedelta

from app.db.models.enums import JobStatus
from app.db.models.job import Job
from app.worker import runner


def _job(db, **kw):
    job = Job(
        type=kw.pop("type", "t.ok"),
        payload=kw.pop("payload", {}),
        status=kw.pop("status", JobStatus.queued),
        **kw,
    )
    db.add(job)
    db.flush()
    return job


def test_run_once_success_marks_succeeded(db):
    seen = []
    runner.JOB_HANDLERS.clear()
    runner.register_handler("t.ok", lambda d, j: seen.append(j.id))
    job = _job(db)
    assert runner.run_once(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.succeeded and seen == [job.id]


def test_run_once_retry_then_fail(db):
    runner.JOB_HANDLERS.clear()

    def boom(d, j):
        raise RuntimeError("nope")

    runner.register_handler("t.boom", boom)
    job = _job(db, type="t.boom")
    runner.run_once(db)  # attempt 1 -> requeued
    db.refresh(job)
    assert job.status == JobStatus.queued and job.attempts == 1 and job.run_after is not None
    assert "nope" in (job.error or "")
    # exhaust attempts
    job.attempts = 4  # WORKER_MAX_ATTEMPTS - 1
    job.run_after = None
    db.flush()
    runner.run_once(db)  # attempt 5 -> failed
    db.refresh(job)
    assert job.status == JobStatus.failed


def test_run_once_unknown_type_fails(db):
    runner.JOB_HANDLERS.clear()
    job = _job(db, type="t.nohandler")
    runner.run_once(db)
    db.refresh(job)
    assert job.status == JobStatus.failed and "no handler" in (job.error or "")


def test_claim_skips_future_run_after(db):
    runner.JOB_HANDLERS.clear()
    runner.register_handler("t.ok", lambda d, j: None)
    _job(db, run_after=datetime.now(UTC) + timedelta(hours=1))
    assert runner.run_once(db) == 0  # not due yet


def test_reap_stale_requeues_running(db):
    job = _job(db, status=JobStatus.running)
    job.updated_at = datetime.now(UTC) - timedelta(seconds=999)
    db.flush()
    assert runner.reap_stale(db, stale_seconds=300) == 1
    db.refresh(job)
    assert job.status == JobStatus.queued
