import subprocess
import sys
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.grant import Grant, GrantAnswer, GrantApplication, GrantMatch
from tests.factories import create_startup, create_user


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], capture_output=True, text=True, check=False
    )


def test_migration_chain_single_head_includes_0107():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    assert "0107_funding_grants" in _alembic("history").stdout


@pytest.fixture()
def ws(db):
    s = create_startup(db, owner=create_user(db))
    db.flush()
    return s


def _grant(db, slug="g"):
    g = Grant(
        slug=slug,
        name="G",
        funder="F",
        kind="cash",
        source_url="https://example.org",
        verified_on=date(2026, 11, 12),
        verification="verified",
    )
    db.add(g)
    db.flush()
    return g


def _bad(db, row):
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(row)
            db.flush()


def test_a_grant_is_matched_once_per_workspace_and_the_score_is_bounded(db, ws):
    g = _grant(db)
    now = datetime.now(UTC)
    db.add(GrantMatch(startup_id=ws.id, grant_id=g.id, score=50, matched_at=now))
    db.flush()
    _bad(db, GrantMatch(startup_id=ws.id, grant_id=g.id, score=60, matched_at=now))
    g2 = _grant(db, "g2")
    _bad(db, GrantMatch(startup_id=ws.id, grant_id=g2.id, score=101, matched_at=now))
    _bad(db, GrantMatch(startup_id=ws.id, grant_id=g2.id, score=10, state="x", matched_at=now))


def test_one_application_per_catalogue_grant_but_many_manual_ones(db, ws):
    g = _grant(db)
    db.add(GrantApplication(startup_id=ws.id, grant_id=g.id, name="A"))
    db.add(GrantApplication(startup_id=ws.id, name="Manual 1"))
    db.add(GrantApplication(startup_id=ws.id, name="Manual 2"))
    db.flush()
    _bad(db, GrantApplication(startup_id=ws.id, grant_id=g.id, name="Again"))
    _bad(db, GrantApplication(startup_id=ws.id, name="Bad", status="done"))


def test_deleting_a_workspace_removes_its_grant_data_but_not_the_catalogue(db, ws):
    g = _grant(db)
    a = GrantApplication(startup_id=ws.id, grant_id=g.id, name="A")
    db.add(a)
    db.flush()
    db.add(GrantAnswer(application_id=a.id, startup_id=ws.id, question="Q?"))
    db.add(GrantMatch(startup_id=ws.id, grant_id=g.id, score=1, matched_at=datetime.now(UTC)))
    db.flush()
    db.delete(ws)
    db.flush()
    assert db.query(GrantApplication).count() == 0 and db.query(GrantAnswer).count() == 0
    assert db.query(GrantMatch).count() == 0 and db.query(Grant).count() >= 1


def test_a_draft_status_outside_the_set_is_refused(db, ws):
    a = GrantApplication(startup_id=ws.id, name="A")
    db.add(a)
    db.flush()
    _bad(
        db,
        GrantAnswer(application_id=a.id, startup_id=ws.id, question="Q?", draft_status="done"),
    )
