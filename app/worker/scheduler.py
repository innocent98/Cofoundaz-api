from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.scheduled_run import ScheduledRun


def _claim(db: Session, task_key: str, scope_key: str, period_key: str) -> bool:
    """Claim (task, scope, period) exactly once. True if newly claimed, False if already taken.

    Inserts inside a SAVEPOINT so an IntegrityError (someone else claimed it) rolls back only
    this insert and leaves the caller's transaction usable.
    """
    try:
        with db.begin_nested():
            db.add(ScheduledRun(task_key=task_key, scope_key=scope_key, period_key=period_key))
            db.flush()
        return True
    except IntegrityError:
        return False
