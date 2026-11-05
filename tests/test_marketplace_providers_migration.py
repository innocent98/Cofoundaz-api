import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import settings

TABLES = {"marketplace_providers", "marketplace_files", "marketplace_provider_portfolio"}


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_migration_chain_single_head_includes_0092():
    heads = _alembic(str(settings.DATABASE_URL), "heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic(str(settings.DATABASE_URL), "history")
    assert "0092_marketplace_providers" in history.stdout


def test_0092_upgrade_downgrade_upgrade_on_a_scratch_database():
    base = make_url(settings.TEST_DATABASE_URL or str(settings.DATABASE_URL))
    name = f"mp_providers_mig_{uuid.uuid4().hex[:8]}"
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
        down = _alembic(
            scratch_url,
            "downgrade",
            "-1",  # one step back: survives the lead re-chaining 0092 after 0090/0091
        )
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
