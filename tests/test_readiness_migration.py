import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import settings

TABLES = {
    "readiness_scores",
    "readiness_score_history",
    "readiness_inputs",
    "deck_reviews",
    "mock_sessions",
    "readiness_narrative",
    "narrative_checks",
}


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_migration_chain_includes_0089_0091_0093_and_0103():
    heads = _alembic(str(settings.DATABASE_URL), "heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1, heads.stdout
    history = _alembic(str(settings.DATABASE_URL), "history")
    assert "0089_readiness_scores" in history.stdout
    assert "0091_readiness_deck_reviews" in history.stdout
    assert "0093_readiness_mock_qa" in history.stdout
    assert "0103_readiness_narrative" in history.stdout


def test_readiness_migrations_upgrade_downgrade_upgrade_on_a_scratch_database():
    base = make_url(settings.TEST_DATABASE_URL or str(settings.DATABASE_URL))
    name = f"readiness_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # no CREATEDB right: say so instead of passing silently
        admin.dispose()
        pytest.skip(f"cannot create a scratch database: {exc}")
    scratch_url = base.set(database=name).render_as_string(hide_password=False)
    eng = create_engine(scratch_url)
    try:
        up = _alembic(scratch_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        assert TABLES <= set(inspect(eng).get_table_names())
        c = _alembic(scratch_url, "downgrade", "0099_funding_cap_table")
        assert c.returncode == 0, c.stderr
        after_c = set(inspect(eng).get_table_names())
        assert not {"readiness_narrative", "narrative_checks"} & after_c
        assert "mock_sessions" in after_c
        c_up = _alembic(scratch_url, "upgrade", "head")
        assert c_up.returncode == 0, c_up.stderr
        top = _alembic(scratch_url, "downgrade", "0092_marketplace_providers")
        assert top.returncode == 0, top.stderr
        mid = set(inspect(eng).get_table_names())
        assert "mock_sessions" not in mid and "deck_reviews" in mid
        up2 = _alembic(scratch_url, "upgrade", "head")
        assert up2.returncode == 0, up2.stderr
        one = _alembic(scratch_url, "downgrade", "0090_funding_core")
        assert one.returncode == 0, one.stderr
        left = set(inspect(eng).get_table_names())
        assert "deck_reviews" not in left and "readiness_scores" in left
        back = _alembic(scratch_url, "upgrade", "head")
        assert back.returncode == 0, back.stderr
        down = _alembic(scratch_url, "downgrade", "0088_content_reports")
        assert down.returncode == 0, down.stderr
        assert not TABLES & set(inspect(eng).get_table_names())
        again = _alembic(scratch_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        assert TABLES <= set(inspect(eng).get_table_names())
        drift = _alembic(scratch_url, "check")
        assert drift.returncode == 0, drift.stdout + drift.stderr
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
