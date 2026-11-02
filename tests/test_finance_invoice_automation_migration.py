import subprocess

import sqlalchemy as sa

from app.core.config import settings

REV = "0066_finance_invoice_automation"
PREV = "0065_finance_payments_core"
NEW_COLUMNS = {"last_reminded_at", "reminder_count", "overdue_notified_at"}


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _invoice_columns() -> dict[str, dict]:
    engine = sa.create_engine(settings.DATABASE_URL)
    try:
        return {c["name"]: c for c in sa.inspect(engine).get_columns("invoices")}
    finally:
        engine.dispose()


def test_single_head_and_revision_in_history():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert REV in history.stdout


def test_upgrade_adds_columns_and_downgrade_removes_them():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    cols = _invoice_columns()
    assert NEW_COLUMNS <= set(cols)
    assert cols["reminder_count"]["nullable"] is False
    assert cols["last_reminded_at"]["nullable"] is True
    assert cols["overdue_notified_at"]["nullable"] is True

    down = _alembic("downgrade", PREV)
    assert down.returncode == 0, down.stderr
    assert NEW_COLUMNS.isdisjoint(_invoice_columns())

    again = _alembic("upgrade", "head")
    assert again.returncode == 0, again.stderr
    assert NEW_COLUMNS <= set(_invoice_columns())
