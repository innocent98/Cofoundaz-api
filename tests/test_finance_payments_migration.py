import subprocess
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.config import settings

REV = "0065_finance_payments_core"
PREV = "0064_sales_coach"


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_single_head_and_revision_in_history():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert REV in history.stdout


def test_upgrade_downgrade_upgrade_round_trips():
    assert _alembic("upgrade", "head").returncode == 0
    down = _alembic("downgrade", PREV)
    assert down.returncode == 0, down.stderr
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr


def test_one_live_connection_per_startup():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    engine = sa.create_engine(settings.DATABASE_URL)
    startup_id = uuid.uuid4()
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                insert = sa.text(
                    "INSERT INTO finance_connections "
                    "(id, startup_id, public_id, provider, status, key_last4, mode) "
                    "VALUES (:id, :sid, :pid, :prov, :status, 'abcd', 'test')"
                )

                def add(provider: str, status: str) -> None:
                    conn.execute(
                        insert,
                        {
                            "id": uuid.uuid4(),
                            "sid": startup_id,
                            "pid": uuid.uuid4().hex,
                            "prov": provider,
                            "status": status,
                        },
                    )

                add("paystack", "active")
                # a disconnected row does not count against the partial unique index
                add("flutterwave", "disconnected")
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        add("flutterwave", "needs_attention")
            finally:
                trans.rollback()
    finally:
        engine.dispose()
