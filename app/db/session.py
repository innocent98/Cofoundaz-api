from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(SessionLocal, "after_commit")
def _publish_pending_realtime(session: Session) -> None:
    pending = session.info.pop("pending_realtime", None)
    activity = session.info.pop("pending_activity", None)
    if not pending and not activity:
        return
    # Imported lazily (not at module top) so tests that monkeypatch
    # app.platform.realtime.publish_* take effect here.
    from app.platform.realtime import publish_activity, publish_notification

    for startup_id, user_id, payload in pending or []:
        publish_notification(startup_id, user_id, payload)  # fail-soft internally
    for startup_id, payload in activity or []:
        publish_activity(startup_id, payload)  # fail-soft internally


@event.listens_for(SessionLocal, "after_rollback")
def _drop_pending_realtime(session: Session) -> None:
    session.info.pop("pending_realtime", None)
    session.info.pop("pending_activity", None)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
