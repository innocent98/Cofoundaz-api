"""Migration 0078: the three plans are seeded with the agreed prices and it round-trips."""

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


def test_plans_are_seeded_and_the_migration_round_trips():
    base = make_url(settings.DATABASE_URL)
    name = f"mig0078_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    try:
        assert _alembic(url, "upgrade", "0077_settings_notif_schedule").returncode == 0
        assert _alembic(url, "upgrade", "head").returncode == 0
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT key, price_monthly_minor, price_annual_minor, ai_credits, seats "
                    "FROM billing_plans ORDER BY rank"
                )
            ).all()
        assert [tuple(r) for r in rows] == [
            ("starter", 0, 0, 500, 1),
            ("growth", 2_400_000, 24_000_000, 5000, 5),
            ("scale", 6_800_000, 68_000_000, 20000, None),
        ]
        assert _alembic(url, "downgrade", "0077_settings_notif_schedule").returncode == 0
        assert "billing_plans" not in sa.inspect(engine).get_table_names()
        assert _alembic(url, "upgrade", "head").returncode == 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
