"""Migration 0077: notification digest / quiet-hours columns default sensibly for existing rows
and the migration round-trips."""

import os
import subprocess
import uuid

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.core.config import settings


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["poetry", "run", "alembic", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DATABASE_URL": url},
    )


def test_existing_preferences_get_defaults_and_round_trip():
    base = make_url(settings.DATABASE_URL)
    name = f"mig0077_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    pid = str(uuid.uuid4())
    try:
        assert _alembic(url, "upgrade", "0076_documents_signer_link_audit").returncode == 0
        with engine.begin() as conn:
            conn.execute(sa.text("SET session_replication_role = replica"))  # skip FKs
            conn.execute(
                sa.text(
                    "INSERT INTO notification_preferences (id, user_id, startup_id, master_email, "
                    "categories) VALUES (:p, :u, :s, true, '{}')"
                ),
                {"p": pid, "u": str(uuid.uuid4()), "s": str(uuid.uuid4())},
            )
        assert _alembic(url, "upgrade", "head").returncode == 0
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT digest_mode, digest_time::text, digest_day, quiet_enabled, "
                    "quiet_from::text, quiet_until::text, timezone "
                    "FROM notification_preferences WHERE id = :p"
                ),
                {"p": pid},
            ).one()
        assert tuple(row) == (
            "realtime",
            "08:00:00",
            0,
            False,
            "20:00:00",
            "06:00:00",
            "Africa/Lagos",
        )
        assert _alembic(url, "downgrade", "0076_documents_signer_link_audit").returncode == 0
        cols = {c["name"] for c in sa.inspect(engine).get_columns("notification_preferences")}
        assert "digest_mode" not in cols and "timezone" not in cols
        assert _alembic(url, "upgrade", "head").returncode == 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
