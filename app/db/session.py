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
    if not pending:
        return
    # Imported lazily (not at module top) so tests that monkeypatch
    # app.platform.realtime.publish_notification take effect here.
    from app.platform.realtime import publish_notification

    for startup_id, user_id, payload in pending:
        publish_notification(startup_id, user_id, payload)  # fail-soft internally


@event.listens_for(SessionLocal, "after_rollback")
def _drop_pending_realtime(session: Session) -> None:
    session.info.pop("pending_realtime", None)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
