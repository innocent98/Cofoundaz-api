import subprocess
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.funding import FundingRound, Investor
from tests.factories import create_startup, create_user


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["poetry", "run", "alembic", *args], capture_output=True, text=True, check=False
    )


def test_migration_chain_single_head_includes_0090():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    assert "0090_funding_core" in _alembic("history").stdout


def _round(db, startup, status="planning"):
    r = FundingRound(startup_id=startup.id, name="Seed", status=status, currency="NGN")
    db.add(r)
    db.flush()
    return r


def _investor(db, startup, rnd, **kw):
    fields = {"name": "A", "type": "angel", "stage": "research", "warm": False}
    fields.update(kw)
    row = Investor(
        startup_id=startup.id,
        round_id=rnd.id,
        source="manual",
        stage_entered_at=datetime.now(UTC),
        position=0,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def ws(db):
    s = create_startup(db, owner=create_user(db))
    db.flush()
    return s


def test_only_one_unfinished_round_per_workspace_but_history_is_allowed(db, ws):
    _round(db, ws, "closed")
    _round(db, ws, "closed")
    _round(db, ws, "open")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            _round(db, ws, "planning")


@pytest.mark.parametrize(
    "kw",
    [
        {"stage": "passed"},  # a Passed investor must carry a reason
        {"stage": "research", "passed_reason": "ghosted"},  # and only Passed may
        {"stage": "limbo"},
        {"check_min_minor": 10, "check_max_minor": 5},
        {"committed_minor": -1},
    ],
)
def test_investor_check_constraints(db, ws, kw):
    rnd = _round(db, ws)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            _investor(db, ws, rnd, **kw)


def test_deleting_a_round_cascades_to_its_investors(db, ws):
    rnd = _round(db, ws)
    inv_id = _investor(db, ws, rnd).id
    db.delete(rnd)
    db.flush()
    db.expunge_all()
    assert db.query(Investor).filter_by(id=inv_id).count() == 0
