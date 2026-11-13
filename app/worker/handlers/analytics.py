"""Analytics & Reports jobs: the frozen report run."""

from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.services.analytics import runs
from app.worker.runner import register_handler


def handle_report_run(db: Session, job: Job) -> None:
    runs.execute(db, job)


register_handler(runs.RUN_JOB, handle_report_run)
