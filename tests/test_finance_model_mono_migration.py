import subprocess
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.config import settings

REV = "0070_finance_model_mono"
PREV = "0069_academy_content"


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["poetry", "run", "alembic", *args], capture_output=True, text=True, check=False
    )


def test_single_head_and_revision_in_history():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1 and REV in heads.stdout


def test_upgrade_downgrade_upgrade_round_trips():
    assert _alembic("upgrade", "head").returncode == 0
    down = _alembic("downgrade", PREV)
    assert down.returncode == 0, down.stderr
    cols = _columns("financial_models")
    assert (
        not {"starting", "overrides", "overrides_rev", "calculated_rev", "recalculated_at"} & cols
    )
    assert "provider_account_hash" not in _columns("finance_connections")
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr


def _columns(table: str) -> set[str]:
    engine = sa.create_engine(settings.DATABASE_URL)
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_new_columns_exist_with_defaults():
    assert _alembic("upgrade", "head").returncode == 0
    assert {
        "starting",
        "overrides",
        "overrides_rev",
        "calculated_rev",
        "recalculated_at",
    } <= _columns("financial_models")
    assert "provider_account_hash" in _columns("finance_connections")


def _insert(conn, startup_id, provider, status):
    conn.execute(
        sa.text(
            "INSERT INTO finance_connections (id, startup_id, public_id, provider, status, key_last4, mode) "
            "VALUES (:id, :sid, :pid, :prov, :status, 'abcd', 'test')"
        ),
        {
            "id": uuid.uuid4(),
            "sid": startup_id,
            "pid": uuid.uuid4().hex,
            "prov": provider,
            "status": status,
        },
    )


def test_bank_link_coexists_with_one_payment_provider_only():
    assert _alembic("upgrade", "head").returncode == 0
    engine = sa.create_engine(settings.DATABASE_URL)
    startup_id = uuid.uuid4()
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                _insert(conn, startup_id, "paystack", "active")
                _insert(conn, startup_id, "mono", "active")  # beside the payment provider
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        _insert(conn, startup_id, "flutterwave", "active")
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def test_one_live_connection_per_bank_account():
    assert _alembic("upgrade", "head").returncode == 0
    engine = sa.create_engine(settings.DATABASE_URL)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(sa.text("SET LOCAL session_replication_role = replica"))

                def add(status: str) -> None:
                    conn.execute(
                        sa.text(
                            "INSERT INTO finance_connections (id, startup_id, public_id, provider, "
                            "status, key_last4, mode, provider_account_hash) "
                            "VALUES (:id, :sid, :pid, 'mono', :st, 'abcd', 'test', 'hash1')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "sid": uuid.uuid4(),
                            "pid": uuid.uuid4().hex,
                            "st": status,
                        },
                    )

                add("disconnected")
                add("active")
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        add("needs_attention")
            finally:
                trans.rollback()
    finally:
        engine.dispose()
