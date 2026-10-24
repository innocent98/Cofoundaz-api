import subprocess

import sqlalchemy as sa

from app.core.config import settings

REV = "0071_finance_slice_e"
PREV = "0070_finance_model_mono"
NEW_COLUMNS = {"account_id", "notes", "payment_details", "last_sent_at"}


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["poetry", "run", "alembic", *args], capture_output=True, text=True, check=False
    )


def _columns() -> set[str]:
    engine = sa.create_engine(settings.DATABASE_URL)
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns("invoices")}
    finally:
        engine.dispose()


def test_single_head_and_revision_in_history():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert REV in history.stdout and PREV in history.stdout


def test_upgrade_downgrade_upgrade_round_trips():
    assert _alembic("upgrade", "head").returncode == 0
    assert NEW_COLUMNS <= _columns()
    down = _alembic("downgrade", PREV)
    assert down.returncode == 0, down.stderr
    assert not NEW_COLUMNS & _columns()
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    assert NEW_COLUMNS <= _columns()
