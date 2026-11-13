import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import settings

TABLES = {
    "marketplace_deliveries",
    "marketplace_threads",
    "marketplace_messages",
    "marketplace_booking_documents",
}
ADDED = {
    "marketplace_bookings": {"overdue_notified_at"},
    "marketplace_orders": {"attention_resolved_at", "attention_resolved_by"},
    "marketplace_files": {"thread_id"},
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


def test_migration_chain_has_one_head_and_it_is_0104():
    heads = _alembic(str(settings.DATABASE_URL), "heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    assert "0104_marketplace_fulfilment" in heads.stdout
    history = _alembic(str(settings.DATABASE_URL), "history")
    assert "0106_legal -> 0104_marketplace_fulfilment" in history.stdout


def _scratch():
    base = make_url(settings.TEST_DATABASE_URL or str(settings.DATABASE_URL))
    name = f"mp_fulfil_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # no CREATEDB right: say so instead of passing silently
        admin.dispose()
        pytest.skip(f"cannot create a scratch database: {exc}")
    return admin, name, base.set(database=name).render_as_string(hide_password=False)


def _drop(admin, name):
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _columns(eng, table):
    return {c["name"] for c in inspect(eng).get_columns(table)}


def test_0104_upgrade_downgrade_upgrade_and_no_drift():
    admin, name, url = _scratch()
    eng = create_engine(url)
    try:
        assert _alembic(url, "upgrade", "head").returncode == 0
        assert TABLES <= set(inspect(eng).get_table_names())
        for table, cols in ADDED.items():
            assert cols <= _columns(eng, table)
        idx = {
            i["name"]
            for t in ("marketplace_bookings", "marketplace_files")
            for i in inspect(eng).get_indexes(t)
        }
        assert {
            "ix_marketplace_bookings_auto_release",
            "ix_marketplace_bookings_overdue",
            "ix_marketplace_files_thread_id",
        } <= idx
        down = _alembic(url, "downgrade", "0106_legal")
        assert down.returncode == 0, down.stderr
        assert not TABLES & set(inspect(eng).get_table_names())
        for table, cols in ADDED.items():
            assert not cols & _columns(eng, table)
        again = _alembic(url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        drift = _alembic(url, "check")
        assert drift.returncode == 0, drift.stdout + drift.stderr
    finally:
        eng.dispose()
        _drop(admin, name)


def test_the_real_schema_enforces_threads_messages_and_delivery_decisions():
    """The unit-test database is built from the models; the constraints are proven here, on a
    database built by the migrations."""
    admin, name, url = _scratch()
    eng = create_engine(url)
    try:
        assert _alembic(url, "upgrade", "head").returncode == 0
        startup, provider, user, booking = (uuid.uuid4() for _ in range(4))
        with eng.begin() as conn:
            for table in ("marketplace_threads", "marketplace_messages", "marketplace_deliveries"):
                for fk in (
                    conn.execute(
                        text(
                            "SELECT conname FROM pg_constraint WHERE conrelid = "
                            f"'{table}'::regclass AND contype = 'f'"
                        )
                    )
                    .scalars()
                    .all()
                ):  # a scratch database: drop the FKs so rows can stand alone
                    conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{fk}"'))

        def thread(conn, booking_id):
            conn.execute(
                text(
                    "INSERT INTO marketplace_threads (id, startup_id, provider_id, booking_id) "
                    "VALUES (:i, :s, :p, :b)"
                ),
                {"i": uuid.uuid4(), "s": startup, "p": provider, "b": booking_id},
            )

        with eng.begin() as conn:
            thread(conn, None)
        with pytest.raises(IntegrityError), eng.begin() as conn:  # one open pair thread
            thread(conn, None)
        with eng.begin() as conn:
            thread(conn, booking)
        with pytest.raises(IntegrityError), eng.begin() as conn:  # one thread per booking
            thread(conn, booking)
        with eng.begin() as conn:
            tid = conn.execute(text("SELECT id FROM marketplace_threads LIMIT 1")).scalar()

        def message(conn, body, side="founder"):
            conn.execute(
                text(
                    "INSERT INTO marketplace_messages "
                    "(id, thread_id, sender_user_id, sender_side, body, file_ids) "
                    "VALUES (:i, :t, :u, :side, :body, '[]')"
                ),
                {"i": uuid.uuid4(), "t": tid, "u": user, "side": side, "body": body},
            )

        with eng.begin() as conn:
            message(conn, "x" * 4000)
        for body, side in (("", "founder"), ("x" * 4001, "founder"), ("hi", "staff")):
            with pytest.raises(DBAPIError), eng.begin() as conn:
                message(conn, body, side)

        def delivery(conn, rnd, decision):
            conn.execute(
                text(
                    "INSERT INTO marketplace_deliveries "
                    "(id, booking_id, round, message, file_ids, submitted_at, decision) "
                    "VALUES (:i, :b, :r, '', '[]', now(), :d)"
                ),
                {"i": uuid.uuid4(), "b": booking, "r": rnd, "d": decision},
            )

        with eng.begin() as conn:
            delivery(conn, 1, None)
            delivery(conn, 2, "auto_released")
        with pytest.raises(IntegrityError), eng.begin() as conn:  # one row per round
            delivery(conn, 1, None)
        with pytest.raises(IntegrityError), eng.begin() as conn:  # a known decision only
            delivery(conn, 3, "maybe")
    finally:
        eng.dispose()
        _drop(admin, name)
