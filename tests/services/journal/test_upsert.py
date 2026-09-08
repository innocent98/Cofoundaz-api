"""Concurrency regression test for the journal entry upsert.

Same rationale as tests/services/test_dashboard_concurrency.py: the per-test `db`
fixture wraps everything in one savepoint that never commits, so it cannot reproduce
a cross-connection race. This test opens its own real, committing `Session`s against
the session-scoped `engine` fixture's connection pool instead.

Regression target: `JournalService.create_entry` used to SELECT for an existing entry
and then INSERT. Two concurrent autosaves for the same (startup, founder, date) could
both pass the check and both INSERT; the loser hit
`uq_journal_entries_startup_founder_date` and raised `IntegrityError`, surfacing as a
500. `_upsert_mood_log` had the same race. Both now use a single
`INSERT ... ON CONFLICT DO UPDATE`, so the database resolves the conflict atomically.
"""

import threading
import uuid
from datetime import date

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.enums import JournalMood, MembershipRole
from app.db.models.journal import JournalEntry, MoodLog
from app.db.models.startup import Startup
from app.db.models.user import User
from app.schemas.journal import JournalEntryCreate
from app.services.journal.service import JournalService
from tests.factories import create_membership, create_startup, create_user

ENTRY_DATE = date(2026, 9, 1)


def test_concurrent_same_day_saves_produce_one_row(engine: Engine):
    setup = Session(bind=engine)
    startup_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        create_membership(setup, user, startup, role=MembershipRole.founder)
        setup.commit()
        startup_id = startup.id
        user_id = user.id
    finally:
        setup.close()

    try:
        barrier = threading.Barrier(2)
        results: list[BaseException | None] = []
        results_lock = threading.Lock()

        def attempt(text: str) -> None:
            session = Session(bind=engine)
            outcome: BaseException | None
            try:
                data = JournalEntryCreate(
                    date=ENTRY_DATE,
                    content=text,
                    mood=JournalMood.okay,
                    stress=5,
                )
                barrier.wait(timeout=5)
                try:
                    JournalService.create_entry(
                        session,
                        user_id=user_id,
                        startup_id=startup_id,
                        data=data,
                    )
                except BaseException as exc:  # noqa: BLE001 - captured for the assert
                    session.rollback()
                    outcome = exc
                else:
                    session.commit()
                    outcome = None
            finally:
                session.close()
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt, args=(f"autosave {i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 2, "both threads must finish"
        assert all(e is None for e in results), f"neither save should raise: {results}"

        verify = Session(bind=engine)
        try:
            entries = (
                verify.query(JournalEntry)
                .filter_by(startup_id=startup_id, founder_id=user_id, date=ENTRY_DATE)
                .count()
            )
            assert entries == 1, "exactly one journal entry, not one per racer"

            moods = (
                verify.query(MoodLog)
                .filter_by(startup_id=startup_id, founder_id=user_id, date=ENTRY_DATE)
                .count()
            )
            assert moods == 1, "exactly one mood log, not one per racer"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                cleanup.query(Startup).filter(Startup.id == startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
