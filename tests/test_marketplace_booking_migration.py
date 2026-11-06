import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import settings

TABLES = {
    "marketplace_bookings",
    "marketplace_orders",
    "marketplace_ledger_entries",
    "marketplace_webhook_events",
    "marketplace_consents",
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


def test_migration_chain_single_head_includes_0101():
    heads = _alembic(str(settings.DATABASE_URL), "heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic(str(settings.DATABASE_URL), "history")
    assert "0101_marketplace_booking" in history.stdout


def _scratch():
    base = make_url(settings.TEST_DATABASE_URL or str(settings.DATABASE_URL))
    name = f"mp_booking_mig_{uuid.uuid4().hex[:8]}"
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


def test_0101_upgrade_downgrade_upgrade_on_a_scratch_database():
    admin, name, url = _scratch()
    eng = create_engine(url)
    try:
        assert _alembic(url, "upgrade", "head").returncode == 0
        assert TABLES <= set(inspect(eng).get_table_names())
        down = _alembic(url, "downgrade", "0101_marketplace_booking-1")
        assert down.returncode == 0, down.stderr
        assert not TABLES & set(inspect(eng).get_table_names())
        with eng.connect() as conn:
            fn = conn.execute(
                text(
                    "SELECT count(*) FROM pg_proc WHERE proname = 'marketplace_ledger_reject_change'"
                )
            ).scalar()
            seq = conn.execute(
                text("SELECT count(*) FROM pg_class WHERE relname = 'marketplace_receipt_seq'")
            ).scalar()
        assert (fn, seq) == (0, 0)  # the downgrade leaves nothing behind
        again = _alembic(url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        drift = _alembic(url, "check")
        assert drift.returncode == 0, drift.stdout + drift.stderr
    finally:
        eng.dispose()
        _drop(admin, name)


def test_the_ledger_is_append_only_in_the_real_schema():
    """The unit-test database is built from the models and has no trigger, so the trigger is
    proven here, on a database built by the migrations."""
    admin, name, url = _scratch()
    eng = create_engine(url)
    try:
        assert _alembic(url, "upgrade", "head").returncode == 0
        entry = uuid.uuid4()
        with eng.begin() as conn:
            for fk in (
                conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = "
                        "'marketplace_ledger_entries'::regclass AND contype = 'f'"
                    )
                )
                .scalars()
                .all()
            ):  # a scratch database: drop the FKs so one row can stand alone
                conn.execute(text(f'ALTER TABLE marketplace_ledger_entries DROP CONSTRAINT "{fk}"'))
            conn.execute(
                text(
                    "INSERT INTO marketplace_ledger_entries "
                    "(id, order_id, provider_id, kind, amount_minor, currency, external_ref) "
                    "VALUES (:i, :o, :p, 'charge', 1000, 'NGN', 'ref-1')"
                ),
                {"i": entry, "o": uuid.uuid4(), "p": uuid.uuid4()},
            )
        for sql in (
            "UPDATE marketplace_ledger_entries SET amount_minor = 1 WHERE id = :i",
            "DELETE FROM marketplace_ledger_entries WHERE id = :i",
        ):
            with pytest.raises(DBAPIError) as exc:
                with eng.begin() as conn:
                    conn.execute(text(sql), {"i": entry})
            assert "append-only" in str(exc.value)
        with eng.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT amount_minor FROM marketplace_ledger_entries WHERE id = :i"),
                    {"i": entry},
                ).scalar()
                == 1000
            )
            n = conn.execute(text("SELECT nextval('marketplace_receipt_seq')")).scalar()
        assert n == 1001  # receipts start at MPR-<year>-1001
    finally:
        eng.dispose()
        _drop(admin, name)
