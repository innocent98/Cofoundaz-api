"""Migration 0074: existing files become plain uploads (no source), and it round-trips."""

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


def test_existing_files_have_no_source_and_it_round_trips():
    base = make_url(settings.DATABASE_URL)
    name = f"mig0074_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    try:
        assert _alembic(url, "upgrade", "0073_documents_library_core").returncode == 0
        startup, file = str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(sa.text("SET session_replication_role = replica"))  # skip FKs
            conn.execute(
                sa.text("INSERT INTO startups (id, created_by) VALUES (:s, :u)"),
                {"s": startup, "u": str(uuid.uuid4())},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO document_files (id, startup_id, filename, content_type, "
                    "size_bytes, storage_key) VALUES (:i, :s, 'a.pdf', 'application/pdf', 2, 'k')"
                ),
                {"i": file, "s": startup},
            )
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT source_document_id, source_version FROM document_files")
            ).one()
        assert tuple(row) == (None, None)
        down = _alembic(url, "downgrade", "0073_documents_library_core")
        assert down.returncode == 0, down.stderr
        assert _alembic(url, "upgrade", "head").returncode == 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
