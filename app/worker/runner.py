from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.enums import JobStatus
from app.db.models.job import Job

Handler = Callable[[Session, Job], None]
JOB_HANDLERS: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    JOB_HANDLERS[job_type] = handler


def backoff_seconds(attempts: int) -> int:
    """Exponential backoff, capped at 1h. attempts is 1-based (post-increment)."""
    delay: int = 30 * (2 ** (attempts - 1))
    return min(delay, 3600)


def reap_stale(db: Session, *, stale_seconds: int) -> int:
    """Re-queue jobs stuck in RUNNING past the stale window (crashed worker)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    n: int = (
        db.query(Job)
        .filter(Job.status == JobStatus.running, Job.updated_at < cutoff)
        .update({Job.status: JobStatus.queued}, synchronize_session=False)
    )
    db.commit()
    return n


def claim_batch(db: Session, *, batch: int, stale_seconds: int) -> list[Job]:
    reap_stale(db, stale_seconds=stale_seconds)
    now = datetime.now(UTC)
    jobs = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.queued,
            (Job.run_after.is_(None)) | (Job.run_after <= now),
        )
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(batch)
        .all()
    )
    for job in jobs:
        job.status = JobStatus.running
        job.attempts = job.attempts + 1
    db.commit()  # publish the claim so a crash leaves rows RUNNING, not lost
    return jobs


def _finalize_success(db: Session, job: Job) -> None:
    job.status = JobStatus.succeeded
    job.error = None
    db.commit()


def _finalize_failure(db: Session, job: Job, exc: Exception) -> None:
    job.error = str(exc)
    if job.attempts < settings.WORKER_MAX_ATTEMPTS:
        job.status = JobStatus.queued
        job.run_after = datetime.now(UTC) + timedelta(seconds=backoff_seconds(job.attempts))
    else:
        job.status = JobStatus.failed
    db.commit()


def _finalize_terminal_failure(db: Session, job: Job, exc: Exception) -> None:
    """Fail a job outright, bypassing the retry/backoff path.

    Used for errors no retry could ever fix (e.g. no registered handler for
    the job's type) — retrying only burns attempts until the normal backoff
    path happens to hit the attempts ceiling, which is both slow and not what
    a misconfiguration error means.
    """
    job.error = str(exc)
    job.status = JobStatus.failed
    db.commit()


def run_once(db: Session) -> int:
    """Claim one batch and run each job in its own transaction. Returns count processed.

    Each handler runs inside its own SAVEPOINT (db.begin_nested()) rather than
    relying on a bare db.rollback() on failure: under the production SessionLocal
    (autocommit=False), a bare rollback on the *session's* transaction would be
    scoped correctly, but under the shared test `db` fixture — which nests the
    session inside an outer per-test SAVEPOINT via
    join_transaction_mode="create_savepoint" — a full-session rollback can unwind
    more than the failing handler's writes. A per-job begin_nested() confines a
    handler failure to that job alone in both environments, and leaves the
    already-committed claim (and any prior/later jobs in the batch) untouched.
    """
    jobs = claim_batch(
        db, batch=settings.WORKER_BATCH_SIZE, stale_seconds=settings.WORKER_STALE_SECONDS
    )
    for job in jobs:
        handler = JOB_HANDLERS.get(job.type)
        if handler is None:
            _finalize_terminal_failure(db, job, RuntimeError(f"no handler for {job.type!r}"))
            continue
        try:
            with db.begin_nested():
                handler(db, job)
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the loop
            log.warning(f"[worker] job {job.id} ({job.type}) failed: {exc}")
            _finalize_failure(db, job, exc)
        else:
            _finalize_success(db, job)
    return len(jobs)
