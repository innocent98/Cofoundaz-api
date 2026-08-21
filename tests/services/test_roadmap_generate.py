"""Tests for generate_roadmap: create-once roadmap generation from the stage catalog.

test_generate_create_once_under_race is a genuine cross-connection race test, not a
same-transaction check. The per-test `db` fixture wraps everything in one savepoint
that never commits, so it can't reproduce a real race between two DB connections (see
tests/services/assessment/test_complete_concurrency.py for the same rationale). This
test opens its own real, committing `Session`s against the session-scoped `engine`
fixture instead.

Regression target: generate_roadmap's create-once claim is a single
`INSERT ... ON CONFLICT (startup_id) DO NOTHING RETURNING id` (see
app/services/roadmap/service.py). Two concurrent callers generating a roadmap for the
same startup must produce exactly one Roadmap row with exactly one un-duplicated phase
tree, and both callers must resolve to the SAME roadmap id. A naive
"check-then-insert" (query for an existing roadmap, insert only if absent) would let
both callers pass the check before either commits, producing two Roadmap rows (or a
duplicated phase tree if the unique constraint silently rejected only the second
insert without the winner returning its rows first). This test fails against that
naive version and passes against the atomic on_conflict_do_nothing claim.
"""

import threading
import uuid
from datetime import date

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.enums import StartupStage
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.roadmap.service import generate_roadmap
from app.services.roadmap.templates import STAGE_TEMPLATES
from tests.factories import create_startup, create_user


def test_generate_builds_the_stage_tree(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)

    r = generate_roadmap(db, startup)
    db.flush()

    assert r.stage == StartupStage.validation
    assert r.template_key == "stage.validation"
    assert r.template_version == 1
    assert db.query(RoadmapPhase).filter_by(roadmap_id=r.id).count() >= 1
    assert db.query(RoadmapMilestone).count() >= 1
    assert db.query(RoadmapTask).count() >= 1

    # dates derive from week offsets off today; first phase starts today
    first_phase = (
        db.query(RoadmapPhase).filter_by(roadmap_id=r.id).order_by(RoadmapPhase.order).first()
    )
    assert first_phase.starts_on == date.today()


def test_generate_is_create_once(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)

    r1 = generate_roadmap(db, startup)
    db.flush()
    phase_count = db.query(RoadmapPhase).count()

    r2 = generate_roadmap(db, startup)
    db.flush()

    assert r2.id == r1.id
    assert db.query(Roadmap).count() == 1
    assert db.query(RoadmapPhase).count() == phase_count  # no duplication


def test_generate_emits_event(db, monkeypatch):
    events = []
    from app.platform import events as events_mod

    monkeypatch.setattr(events_mod.event_bus, "publish", lambda e, p: events.append((e, p)))
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.idea)
    generate_roadmap(db, startup)

    assert any(e == "roadmap.generated" for e, _ in events)


def test_generate_falls_back_to_idea_template_when_stage_missing(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=None)

    r = generate_roadmap(db, startup)
    db.flush()

    assert r.template_key == "stage.idea"


def test_generate_create_once_under_race(engine: Engine):
    """Two independent connections generating concurrently => exactly one roadmap."""
    setup = Session(bind=engine)
    startup_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user, stage=StartupStage.validation)
        setup.commit()
        startup_id = startup.id
        user_id = user.id

        barrier = threading.Barrier(2)
        results: list[tuple[uuid.UUID | None, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            outcome: tuple[uuid.UUID | None, BaseException | None]
            try:
                st = session.query(Startup).filter(Startup.id == startup_id).one()
                barrier.wait(timeout=5)
                try:
                    roadmap = generate_roadmap(session, st)
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    session.rollback()
                    outcome = (None, exc)
                else:
                    session.commit()
                    outcome = (roadmap.id, None)
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

        id0, id1 = results[0][0], results[1][0]
        assert id0 == id1, "both callers must resolve to the same roadmap id"

        verify = Session(bind=engine)
        try:
            assert (
                verify.query(Roadmap).filter_by(startup_id=startup_id).count() == 1
            ), "exactly one Roadmap row, not one per racer"

            roadmap = verify.query(Roadmap).filter_by(startup_id=startup_id).one()
            expected_phases = len(STAGE_TEMPLATES["validation"]["phases"])
            phase_count = verify.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id).count()
            assert (
                phase_count == expected_phases
            ), f"expected {expected_phases} phases (no duplication), got {phase_count}"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                # Roadmap FK cascades delete phases/milestones/tasks.
                cleanup.query(Roadmap).filter_by(startup_id=startup_id).delete()
                cleanup.query(Startup).filter_by(id=startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter_by(id=user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        setup.close()
