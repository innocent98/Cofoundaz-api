"""Analytics & Reports jobs: the frozen report run, the export render and the daily purge."""

import uuid

from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.services.analytics import exports, runs
from app.worker.runner import register_handler
from app.worker.scheduler import SCHED_ANALYTICS_PURGE


def handle_report_run(db: Session, job: Job) -> None:
    runs.execute(db, job)


def handle_export_render(db: Session, job: Job) -> None:
    exports.render(db, uuid.UUID(str(job.payload["export_id"])))


def handle_purge(db: Session, job: Job) -> None:  # noqa: ARG001
    exports.purge(db)


register_handler(runs.RUN_JOB, handle_report_run)
register_handler(exports.RENDER_JOB, handle_export_render)
register_handler(SCHED_ANALYTICS_PURGE, handle_purge)
