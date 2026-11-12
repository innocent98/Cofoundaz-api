"""Funding Hub jobs: term sheet extraction and flag wording."""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.job import Job
from app.services.funding import term_extract, term_sheets
from app.worker.runner import register_handler


def _last(job: Job) -> bool:
    return (job.attempts or 0) >= settings.WORKER_MAX_ATTEMPTS


def handle_extract(db: Session, job: Job) -> None:
    term_extract.run_extract(db, job, last_attempt=_last(job))


def handle_wording(db: Session, job: Job) -> None:
    term_extract.run_wording(db, job, last_attempt=_last(job))


register_handler(term_sheets.EXTRACT_JOB, handle_extract)
register_handler(term_sheets.WORDING_JOB, handle_wording)
