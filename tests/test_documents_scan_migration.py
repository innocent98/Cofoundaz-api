"""Migration 0072: only files a signature request already depends on are trusted as `clean`."""

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


def test_backfill_marks_only_referenced_files_clean_and_round_trips():
    base = make_url(settings.DATABASE_URL)
    name = f"mig0072_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    try:
        assert _alembic(url, "upgrade", "0071_finance_slice_e").returncode == 0
        referenced, loose, req = (str(uuid.uuid4()) for _ in range(3))
        startup = str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(sa.text("SET session_replication_role = replica"))  # skip FKs
            for fid in (referenced, loose):
                conn.execute(
                    sa.text(
                        "INSERT INTO document_files (id, startup_id, filename, content_type, "
                        "size_bytes, storage_key, url) VALUES (:i, :s, 'a.pdf', "
                        "'application/pdf', 2, :k, 'https://public.example/a.pdf')"
                    ),
                    {"i": fid, "s": startup, "k": f"documents/{startup}/{fid}.pdf"},
                )
            conn.execute(
                sa.text(
                    "INSERT INTO signature_requests (id, startup_id, file_id, title) "
                    "VALUES (:r, :s, :f, 'NDA')"
                ),
                {"r": req, "s": startup, "f": referenced},
            )
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with engine.connect() as conn:
            rows = dict(
                conn.execute(sa.text("SELECT id::text, scan_status FROM document_files")).all()
            )
            private = conn.execute(sa.text("SELECT DISTINCT is_private FROM document_files")).all()
        assert rows == {referenced: "clean", loose: "pending"}
        assert [p[0] for p in private] == [False]  # the backfill job moves them, not the migration
        down = _alembic(url, "downgrade", "0071_finance_slice_e")
        assert down.returncode == 0, down.stderr
        assert _alembic(url, "upgrade", "head").returncode == 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
