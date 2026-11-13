"""Worker handler for the Marketplace timers (booking holds, 48-hour acceptance, refund retries)."""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.services.marketplace import timers
from app.worker.runner import register_handler

SCHED_MARKETPLACE_TIMERS = "marketplace.timers"


def handle_marketplace_timers(db: Session, job: Job) -> None:  # noqa: ARG001
    result = timers.tick(db, datetime.now(UTC))
    logging.getLogger(__name__).info("marketplace timers: %s", result)


register_handler(SCHED_MARKETPLACE_TIMERS, handle_marketplace_timers)
