"""Migration 0073: existing documents get search text, a snapshot of their current version and
their folders; the whole thing round-trips."""

import json
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


def test_backfill_and_round_trip():
    base = make_url(settings.DATABASE_URL)
    name = f"mig0073_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    try:
        assert _alembic(url, "upgrade", "0072_documents_scan_status").returncode == 0
        startup, doc, empty, file = (str(uuid.uuid4()) for _ in range(4))
        sections = [
            {"id": "s1", "heading": "Summary", "body": "We sell shovels"},
            {"id": "s2", "heading": "Team", "body": "Two founders"},
        ]
        with engine.begin() as conn:
            conn.execute(sa.text("SET session_replication_role = replica"))  # skip FKs
            conn.execute(
                sa.text("INSERT INTO startups (id, created_by) VALUES (:s, :u)"),
                {"s": startup, "u": str(uuid.uuid4())},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO documents (id, startup_id, kind, status, ai_generated, folder, "
                    "title, sections, version) VALUES (:i, :s, 'custom', 'final', false, 'Legal', "
                    "'Plan', CAST(:sec AS jsonb), 4)"
                ),
                {"i": doc, "s": startup, "sec": json.dumps(sections)},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO documents (id, startup_id, kind, status, ai_generated, title, "
                    "sections, version) VALUES (:i, :s, 'custom', 'draft', false, '', '[]', 1)"
                ),
                {"i": empty, "s": startup},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO document_files (id, startup_id, filename, content_type, "
                    "size_bytes, storage_key, folder) VALUES (:i, :s, 'a.pdf', 'application/pdf', "
                    "2, 'k', 'Finance')"
                ),
                {"i": file, "s": startup},
            )
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with engine.connect() as conn:
            text = dict(conn.execute(sa.text("SELECT id::text, search_text FROM documents")).all())
            versions = conn.execute(
                sa.text("SELECT document_id::text, version, title, status FROM document_versions")
            ).all()
            folders = {r[0] for r in conn.execute(sa.text("SELECT path FROM document_folders"))}
            tags = conn.execute(sa.text("SELECT DISTINCT tags::text FROM documents")).all()
            trashed = conn.execute(
                sa.text("SELECT count(*) FROM documents WHERE trashed_at IS NOT NULL")
            ).scalar()
        assert text[doc] == "Summary We sell shovels Team Two founders" and text[empty] == ""
        assert sorted(versions) == sorted([(doc, 4, "Plan", "final"), (empty, 1, "", "draft")])
        assert folders == {"Legal", "Finance"}
        assert [t[0] for t in tags] == ["[]"] and trashed == 0
        down = _alembic(url, "downgrade", "0072_documents_scan_status")
        assert down.returncode == 0, down.stderr
        assert _alembic(url, "upgrade", "head").returncode == 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
