"""Concurrency regression test for the business canvas lazy-create race.

Same rationale as tests/services/test_dashboard_concurrency.py and
tests/services/assessment/test_complete_concurrency.py: the per-test `db` fixture wraps
everything in one savepoint that never commits, so it can't reproduce a cross-connection
race. This test opens its own real, committing `Session`s against the session-scoped
`engine` fixture's connection pool instead.

Regression target: `get_or_create_canvas` (app/services/business/service.py:18-47) used to do
an unguarded check-then-INSERT against the `uq_business_canvas_startup_type` unique constraint.
Two concurrent first-loads of the same `(startup_id, type)` (e.g. two tabs both opening a canvas
that's never been created) can both pass its `row is None` check and both INSERT; the loser's
flush raises `IntegrityError`. `get_or_create_canvas` now wraps the insert in a SAVEPOINT
(`db.begin_nested()`) and re-selects on `IntegrityError` instead -- mirroring
`app/services/dashboard/service.py::_get_or_generate_today_race_safe`. This test fails (one
racer's call raises, or two `business_canvases` rows exist) against the naive unguarded version,
and passes against the race-safe one.
"""

import threading
import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.business import BusinessCanvas
from app.db.models.enums import CanvasType
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.business.service import get_or_create_canvas
from tests.factories import create_startup, create_user


def test_concurrent_get_or_create_canvas_no_duplicate_row(engine: Engine):
    setup = Session(bind=engine)
    startup_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        setup.commit()
        startup_id = startup.id
        user_id = user.id
    finally:
        setup.close()

    try:
        barrier = threading.Barrier(2)
        # Capture plain values inside the thread, before the session closes and
        # expires/detaches the ORM instance -- not the ORM object itself.
        results: list[tuple[dict[str, object] | None, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            outcome: tuple[dict[str, object] | None, BaseException | None]
            try:
                s = session.query(Startup).filter(Startup.id == startup_id).one()
                barrier.wait(timeout=5)
                try:
                    out = get_or_create_canvas(session, s, CanvasType.business_model)
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    session.rollback()
                    outcome = (None, exc)
                else:
                    session.commit()
                    outcome = ({"startup_id": out.startup_id, "type": out.type}, None)
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
            assert out is not None, "get_or_create_canvas must not return None"
            assert out["startup_id"] == startup_id
            assert out["type"] == CanvasType.business_model

        verify = Session(bind=engine)
        try:
            count = (
                verify.query(BusinessCanvas)
                .filter_by(startup_id=startup_id, type=CanvasType.business_model)
                .count()
            )
            assert count == 1, "exactly one business_canvases row, not one per racer"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                # Cascades: startups -> startup_profiles, business_canvases (FK CASCADE).
                cleanup.query(Startup).filter(Startup.id == startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
