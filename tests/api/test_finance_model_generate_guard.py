"""POST /finance/model/generate: one generation per workspace at a time (gap row 15)."""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import FinancialModelStatus, MembershipRole
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job
from app.services.finance import model_service as svc
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member
from tests.factories import create_startup, create_user


def _generate(client, h):
    return client.post(f"{BASE}/model/generate", headers=h)


def _jobs(db, startup_id):
    return db.query(Job).filter_by(startup_id=startup_id, type=svc.MODEL_JOB_TYPE).count()


def test_second_call_while_generating_is_409_and_enqueues_nothing(client, db):
    _u, s, h = _member(db)
    first = _generate(client, h)
    assert first.status_code == 202, first.text
    second = _generate(client, h)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "MODEL_ALREADY_GENERATING"
    assert _jobs(db, s.id) == 1
    assert db.query(FinancialModel).filter_by(startup_id=s.id).count() == 1


def test_a_different_team_member_is_blocked_too(client, db):
    _f, startup, fh = _member(db)
    assert _generate(client, fh).status_code == 202
    _u, _s, h = _member(db, role=MembershipRole.team_member, startup=startup)
    assert _generate(client, h).status_code == 409


def test_other_workspaces_are_independent(client, db):
    _u, _s, h = _member(db)
    _x, _s2, other = _member(db)
    assert _generate(client, h).status_code == 202
    assert _generate(client, other).status_code == 202


@pytest.mark.parametrize("final", [FinancialModelStatus.complete, FinancialModelStatus.failed])
def test_a_finished_model_allows_the_next_one(client, db, final):
    _u, s, h = _member(db)
    row = svc.create_model(db, startup_id=s.id, created_by=_u.id)
    row.status = final
    db.flush()
    assert _generate(client, h).status_code == 202


def test_a_lost_job_does_not_block_forever(client, db):
    _u, s, h = _member(db)
    old = svc.create_model(db, startup_id=s.id, created_by=_u.id)
    old.created_at = datetime.now(UTC) - svc.STALE_AFTER - timedelta(minutes=1)
    db.flush()
    assert _generate(client, h).status_code == 202
    db.refresh(old)
    assert old.status == FinancialModelStatus.failed and old.error == "Generation timed out"


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_mentor_and_investor_are_403_before_the_guard(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert _generate(client, h).status_code == 403


def test_the_database_itself_refuses_a_second_generating_row(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(FinancialModel(startup_id=s.id, status=FinancialModelStatus.generating))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(FinancialModel(startup_id=s.id, status=FinancialModelStatus.generating))
    # finished rows are unlimited
    db.add(FinancialModel(startup_id=s.id, status=FinancialModelStatus.complete))
    db.add(FinancialModel(startup_id=s.id, status=FinancialModelStatus.failed))
    db.flush()


def test_concurrent_calls_start_exactly_one(engine: Engine):
    """Two real committing sessions race create_model for one workspace."""
    setup = Session(bind=engine)
    user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
    startup = create_startup(setup, owner=user)
    setup.commit()
    startup_id, user_id = startup.id, user.id
    setup.close()  # an idle open transaction here would block the suite's table teardown
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        session = Session(bind=engine)
        try:
            barrier.wait(timeout=5)
            try:
                svc.create_model(session, startup_id=startup_id, created_by=user_id)
                session.commit()
                result = "started"
            except svc.ModelAlreadyGenerating:
                session.rollback()
                result = "blocked"
        finally:
            session.close()
        with lock:
            outcomes.append(result)

    try:
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert sorted(outcomes) == ["blocked", "started"]
        check = Session(bind=engine)
        try:
            assert check.query(FinancialModel).filter_by(startup_id=startup_id).count() == 1
            assert check.query(Job).filter_by(startup_id=startup_id).count() == 1
        finally:
            check.close()
    finally:
        cleanup = Session(bind=engine)
        cleanup.query(Job).filter_by(startup_id=startup_id).delete()
        cleanup.query(FinancialModel).filter_by(startup_id=startup_id).delete()
        cleanup.commit()
        cleanup.close()
        tidy = Session(bind=engine)
        from app.db.models.startup import Startup
        from app.db.models.user import User

        tidy.execute(
            __import__("sqlalchemy").text("DELETE FROM memberships WHERE startup_id = :s"),
            {"s": startup_id},
        )
        tidy.query(Startup).filter_by(id=startup_id).delete()
        tidy.query(User).filter_by(id=user_id).delete()
        tidy.commit()
        tidy.close()
