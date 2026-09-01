"""Concurrency regression test for the dashboard summary's mission cold-start race.

Same rationale as tests/services/assessment/test_complete_concurrency.py and
tests/services/auth/test_sessions_concurrency.py: the per-test `db` fixture wraps
everything in one savepoint that never commits, so it can't reproduce a cross-connection
race. This test opens its own real, committing `Session`s against the session-scoped
`engine` fixture's connection pool instead.

Regression target: `get_or_generate_today` (app/services/mission/service.py:84-106) does
an unguarded check-then-INSERT against the `uq_mission_startup_date` unique constraint.
Two concurrent first-loads-of-a-new-day for the same startup (two summary polls, or a
summary racing `GET /missions/today`) can both pass its `existing is None` check and both
INSERT; the loser's flush raises `IntegrityError`, which -- unguarded -- marks the whole
shared Session rollback-only, cascading into `PendingRollbackError` on every later
section and on the endpoint's final `db.commit()`. `app/services/dashboard/service.py`'s
`_get_or_generate_today_race_safe` wraps the call in a SAVEPOINT and re-selects on
`IntegrityError` instead. This test fails (one racer's `get_summary` call raises, or two
`missions` rows exist) against the naive unguarded version, and passes against the
race-safe one.
"""

import threading
import uuid
from datetime import date

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.mission import Mission
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.dashboard.service import get_summary
from tests.factories import create_phase, create_roadmap, create_startup, create_user


def test_concurrent_dashboard_summary_mission_cold_start_no_500(engine: Engine):
    setup = Session(bind=engine)
    startup_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        roadmap = create_roadmap(setup, startup=startup)
        create_phase(setup, roadmap=roadmap)
        setup.commit()
        startup_id = startup.id
        user_id = user.id
    finally:
        setup.close()

    try:
        barrier = threading.Barrier(2)
        results: list[tuple[dict | None, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            outcome: tuple[dict | None, BaseException | None]
            try:
                s = session.query(Startup).filter(Startup.id == startup_id).one()
                u = session.query(User).filter(User.id == user_id).one()
                barrier.wait(timeout=5)
                try:
                    out = get_summary(session, s, u)
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    session.rollback()
                    outcome = (None, exc)
                else:
                    session.commit()  # mirrors app/api/v1/endpoints/dashboard.py:34
                    outcome = (out, None)
            finally:
                session.close()
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 2, "both threads must finish"
        assert all(exc is None for _, exc in results), f"neither call should raise: {results}"

        for out, _ in results:
            assert out is not None
            assert out["mission"] is not None, "mission section must not be None"
            assert out["mission"] != {"error": True}, "mission cold-start race must not 500"

        verify = Session(bind=engine)
        try:
            count = (
                verify.query(Mission)
                .filter_by(startup_id=startup_id, mission_date=date.today())
                .count()
            )
            assert count == 1, "exactly one missions row, not one per racer"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                # Cascades: startups -> startup_profiles, roadmaps -> roadmap_phases,
                # missions -> mission_tasks.
                cleanup.query(Startup).filter(Startup.id == startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
