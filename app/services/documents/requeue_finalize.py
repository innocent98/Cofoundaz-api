"""One-off, idempotent re-queue of stuck executed-PDF jobs.

A request that completed while the worker could not do its job (for example before the worker
shared the uploads volume) keeps `executed.state: "pending"` forever: its finalize job failed or ran
out of attempts and nothing re-queues it. This enqueues `documents.signature_finalize` again for
every completed e-sign v2 request whose executed copy is missing or not yet emailed to every party,
except requests that already have a queued or running finalize job. The handler is resumable and
claims each email, so running this twice (or while a job is live) never double-sends.

    python -m app.services.documents.requeue_finalize --dry-run
    python -m app.services.documents.requeue_finalize
"""

import argparse
import json

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.db.models.document import SignatureRequest, SignatureSigner
from app.db.models.enums import JobStatus, SignatureRequestStatus
from app.db.models.job import Job
from app.db.models.user import User
from app.platform.jobs import job_dispatcher
from app.services.documents.signatures import FINALIZE_JOB


def _stuck_requests(db: Session) -> list[SignatureRequest]:
    unsent_signer = exists().where(
        SignatureSigner.request_id == SignatureRequest.id,
        SignatureSigner.executed_emailed_at.is_(None),
    )
    owner_unsent = and_(
        SignatureRequest.owner_emailed_at.is_(None),
        exists().where(User.id == SignatureRequest.created_by_id, User.email.isnot(None)),
    )
    return list(
        db.query(SignatureRequest)
        .filter(
            SignatureRequest.status == SignatureRequestStatus.complete,
            SignatureRequest.document_hash.isnot(None),  # v2 only; older ones are "legacy"
            or_(SignatureRequest.executed_file_id.is_(None), unsent_signer, owner_unsent),
        )
        .order_by(SignatureRequest.completed_at)
        .all()
    )


def _active_request_ids(db: Session) -> set[str]:
    rows = db.execute(
        select(Job.payload["request_id"].astext).where(
            Job.type == FINALIZE_JOB, Job.status.in_([JobStatus.queued, JobStatus.running])
        )
    )
    return {r for (r,) in rows if r}


def requeue(db: Session, *, dry_run: bool = False) -> dict[str, int]:
    """Enqueue a finalize job for each stuck request without a live one. Returns the counts."""
    stuck = _stuck_requests(db)
    active = _active_request_ids(db)
    todo = [r for r in stuck if str(r.id) not in active]
    if not dry_run:
        for r in todo:
            job_dispatcher.enqueue(db, FINALIZE_JOB, {"request_id": str(r.id)}, r.startup_id)
        db.commit()
    return {
        "stuck": len(stuck),
        "skipped_active_job": len(stuck) - len(todo),
        "enqueued": 0 if dry_run else len(todo),
        "would_enqueue": len(todo) if dry_run else 0,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only; enqueue nothing")
    args = parser.parse_args(argv)
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        print(json.dumps(requeue(db, dry_run=args.dry_run)))  # noqa: T201 - the CLI's output
    finally:
        db.close()


if __name__ == "__main__":
    main()
