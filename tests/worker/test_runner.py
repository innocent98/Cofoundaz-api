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


def test_self_managed_handler_may_commit(db):
    """Handlers registered with manages_transactions=True run outside the runner's SAVEPOINT, so
    they can commit as they go (the outreach send tick and bounce poll; see
    tests/worker/test_sales_outreach_scheduler.py for the real-handler regression)."""
    runner.JOB_HANDLERS.clear()

    def commits(d, j):
        d.add(Job(type="t.side", payload={}))
        d.commit()

    runner.register_handler("t.own", commits, manages_transactions=True)
    own = _job(db, type="t.own")
    runner.run_once(db)
    db.refresh(own)
    assert own.status == JobStatus.succeeded


def test_self_managed_handler_failure_discards_uncommitted_work_and_retries(db):
    runner.JOB_HANDLERS.clear()

    def half_then_boom(d, j):
        d.add(Job(type="t.committed", payload={}))
        d.commit()
        d.add(Job(type="t.uncommitted", payload={}))
        d.flush()
        raise RuntimeError("mid-run")

    runner.register_handler("t.own", half_then_boom, manages_transactions=True)
    job = _job(db, type="t.own")
    runner.run_once(db)
    db.refresh(job)
    assert job.status == JobStatus.queued and "mid-run" in (job.error or "")
    types = {t for (t,) in db.query(Job.type).all()}
    assert "t.committed" in types and "t.uncommitted" not in types


def test_reregistering_without_the_flag_clears_it(db):
    runner.JOB_HANDLERS.clear()
    runner.register_handler("t.x", lambda d, j: None, manages_transactions=True)
    runner.register_handler("t.x", lambda d, j: None)
    assert "t.x" not in runner.SELF_MANAGED
