"""Documents BE-B: versions, trash, tags, search text, folders, per-person grants

Revision ID: 0073_documents_library_core
Revises: 0072_documents_scan_status
Create Date: 2026-10-25

1. documents gains `tags`, `search_text` (heading + body text, backfilled here) and the trash
   stamps `trashed_at` / `trashed_by_id`; document_files gains `tags` and the trash stamps.
2. document_versions: one immutable snapshot per version number. Every existing document gets a
   snapshot of its CURRENT version (older saves were overwritten and cannot be recovered).
3. document_folders: named, nestable folders; items keep `folder` as the folder's full path.
   Every distinct folder string already in use is backfilled as a root folder.
4. document_grants: per-person grants (D-DOC-1) by account or by email, one live grant per
   person per document.

Downgrade drops all of it (grants and snapshots are lost).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0073_documents_library_core"
down_revision = "0072_documents_scan_status"
branch_labels = None
depends_on = None

_TRASHED_BY_DOC = "fk_documents_trashed_by_id_users"
_TRASHED_BY_FILE = "fk_document_files_trashed_by_id_users"


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "tags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
    )
    op.add_column(
        "documents", sa.Column("search_text", sa.Text(), server_default="", nullable=False)
    )
    op.add_column("documents", sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("trashed_by_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        _TRASHED_BY_DOC, "documents", "users", ["trashed_by_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(
        "document_files",
        sa.Column(
            "tags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
    )
    op.add_column(
        "document_files", sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("document_files", sa.Column("trashed_by_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        _TRASHED_BY_FILE,
        "document_files",
        "users",
        ["trashed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE documents SET search_text = COALESCE((SELECT string_agg("
        "COALESCE(s->>'heading', '') || ' ' || COALESCE(s->>'body', ''), ' ') "
        "FROM jsonb_array_elements(documents.sections) AS s), '')"
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), server_default="", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column(
            "sections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_doc"),
    )
    op.create_index("ix_document_versions_startup_id", "document_versions", ["startup_id"])
    op.execute(
        "INSERT INTO document_versions (id, startup_id, document_id, version, title, status, "
        "sections, author_id, note, created_at, updated_at) "
        "SELECT gen_random_uuid(), startup_id, id, version, title, status, sections, "
        "created_by_id, 'Version at the time version history was introduced', updated_at, "
        "updated_at FROM documents"
    )

    op.create_table(
        "document_folders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("path", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["document_folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("startup_id", "path", name="uq_document_folders_path"),
    )
    op.create_index("ix_document_folders_startup_id", "document_folders", ["startup_id"])
    op.execute(
        "INSERT INTO document_folders (id, startup_id, parent_id, name, path, created_at, "
        "updated_at) SELECT gen_random_uuid(), startup_id, NULL, left(folder, 60), folder, now(), "
        "now() FROM (SELECT startup_id, folder FROM documents WHERE folder IS NOT NULL "
        "UNION SELECT startup_id, folder FROM document_files WHERE folder IS NOT NULL) f "
        "WHERE folder <> ''"
    )

    op.create_table(
        "document_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("grantee_user_id", sa.UUID(), nullable=True),
        sa.Column("grantee_email", sa.String(length=255), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False),
        sa.Column("granted_by_id", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["grantee_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_grants_startup_id", "document_grants", ["startup_id"])
    op.create_index("ix_document_grants_document_id", "document_grants", ["document_id"])
    op.create_index("ix_document_grants_grantee_user_id", "document_grants", ["grantee_user_id"])
    op.create_index("ix_document_grants_grantee_email", "document_grants", ["grantee_email"])
    op.create_index(
        "uq_document_grants_active",
        "document_grants",
        ["document_id", "grantee_email"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("document_grants")
    op.drop_table("document_folders")
    op.drop_table("document_versions")
    op.drop_constraint(_TRASHED_BY_FILE, "document_files", type_="foreignkey")
    op.drop_column("document_files", "trashed_by_id")
    op.drop_column("document_files", "trashed_at")
    op.drop_column("document_files", "tags")
    op.drop_constraint(_TRASHED_BY_DOC, "documents", type_="foreignkey")
    op.drop_column("documents", "trashed_by_id")
    op.drop_column("documents", "trashed_at")
    op.drop_column("documents", "search_text")
    op.drop_column("documents", "tags")
