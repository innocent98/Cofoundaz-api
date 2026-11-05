from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.job import Job
from app.services.readiness import deck
from app.worker.runner import register_handler


def handle_deck_review(db: Session, job: Job) -> None:
    """Review one pitch deck PDF. No commit. See `readiness.deck.run_review`."""
    deck.run_review(db, job, last_attempt=(job.attempts or 0) >= settings.WORKER_MAX_ATTEMPTS)


register_handler(deck.JOB_TYPE, handle_deck_review)
