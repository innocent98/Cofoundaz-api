"""Migration 0075: pre-v2 signers count as already notified, old shares keep working, link and
file shares need the new schema (downgrade removes them), and it round-trips."""

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
    name = f"mig0075_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    ids = {k: str(uuid.uuid4()) for k in ("s", "f", "r", "sg", "d", "sh", "sh2")}
    try:
        assert _alembic(url, "upgrade", "0074_documents_templates_export").returncode == 0
        with engine.begin() as conn:
            conn.execute(sa.text("SET session_replication_role = replica"))  # skip FKs
            conn.execute(
                sa.text("INSERT INTO startups (id, created_by) VALUES (:s, :u)"),
                {"s": ids["s"], "u": str(uuid.uuid4())},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO document_files (id, startup_id, filename, content_type, "
                    "size_bytes, storage_key) VALUES (:f, :s, 'a.pdf', 'application/pdf', 2, 'k')"
                ),
                ids,
            )
            conn.execute(
                sa.text(
                    "INSERT INTO signature_requests (id, startup_id, file_id, title) "
                    "VALUES (:r, :s, :f, 'NDA')"
                ),
                ids,
            )
            conn.execute(
                sa.text(
                    "INSERT INTO signature_signers (id, request_id, email, token_hash, position) "
                    "VALUES (:sg, :r, 'a@x.com', 'h1', 0)"
                ),
                ids,
            )
            conn.execute(sa.text("INSERT INTO documents (id, startup_id) VALUES (:d, :s)"), ids)
            conn.execute(
                sa.text(
                    "INSERT INTO document_shares (id, startup_id, document_id, email, "
                    "access_level, token_hash) VALUES (:sh, :s, :d, 'p@x.com', 'view', 'h2')"
                ),
                ids,
            )
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with engine.connect() as conn:
            signer = conn.execute(
                sa.text(
                    "SELECT notified_at = created_at, viewed_at, token_version "
                    "FROM signature_signers"
                )
            ).one()
            req = conn.execute(
                sa.text("SELECT sequential, message, executed_file_id FROM signature_requests")
            ).one()
        assert tuple(signer) == (True, None, 0) and tuple(req) == (False, None, None)

        with engine.begin() as conn:
            conn.execute(sa.text("SET session_replication_role = replica"))
            conn.execute(  # a link-only file share, impossible before 0075
                sa.text(
                    "INSERT INTO document_shares (id, startup_id, file_id, email, access_level, "
                    "token_hash) VALUES (:sh2, :s, :f, NULL, 'view', 'h3')"
                ),
                ids,
            )
            both = conn.execute(  # the CHECK: exactly one target
                sa.text(
                    "SELECT count(*) FROM document_shares WHERE (document_id IS NOT NULL) "
                    "<> (file_id IS NOT NULL)"
                )
            ).scalar()
        assert both == 2

        down = _alembic(url, "downgrade", "0074_documents_templates_export")
        assert down.returncode == 0, down.stderr
        with engine.connect() as conn:
            left = conn.execute(sa.text("SELECT email FROM document_shares")).scalars().all()
        assert left == ["p@x.com"]  # the file / link-only share could not survive
        assert _alembic(url, "upgrade", "head").returncode == 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
