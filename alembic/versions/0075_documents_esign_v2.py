"""Documents BE-D: e-sign v2 (executed PDF, signing order, viewed state) and link shares

Revision ID: 0075_documents_esign_v2
Revises: 0074_documents_templates_export
Create Date: 2026-10-25

signature_requests: `message`, `sequential`, `document_hash`, `executed_file_id` (FK document_files,
SET NULL), `executed_hash`, `owner_emailed_at`.
signature_signers: `token_version` (link rotation), `notified_at` (link released; backfilled to
`created_at` because every pre-v2 signer was emailed on creation), `viewed_at`,
`executed_emailed_at`, `link_copies` (audit of links handed to editors).
document_shares: `file_id` (FK document_files, CASCADE) so uploaded files are shareable,
`document_id` and `email` become nullable (email NULL = anyone with the link), and a CHECK keeps
exactly one of document_id / file_id set.

Downgrade removes the new columns and the CHECK. Link-only and file shares cannot exist in the old
schema, so they are deleted first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0075_documents_esign_v2"
down_revision = "0074_documents_templates_export"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signature_requests", sa.Column("message", sa.Text(), nullable=True))
    op.add_column(
        "signature_requests",
        sa.Column("sequential", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("signature_requests", sa.Column("document_hash", sa.String(64), nullable=True))
    op.add_column(
        "signature_requests",
        sa.Column("executed_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("signature_requests", sa.Column("executed_hash", sa.String(64), nullable=True))
    op.add_column(
        "signature_requests",
        sa.Column("owner_emailed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_signature_requests_executed_file_id_document_files",
        "signature_requests",
        "document_files",
        ["executed_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_signature_requests_executed_file_id"),
        "signature_requests",
        ["executed_file_id"],
    )

    op.add_column(
        "signature_signers",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "signature_signers", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "signature_signers", sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "signature_signers",
        sa.Column("executed_emailed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_signers",
        sa.Column(
            "link_copies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.execute("UPDATE signature_signers SET notified_at = created_at")

    op.add_column(
        "document_shares", sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.alter_column("document_shares", "document_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column("document_shares", "email", existing_type=sa.String(255), nullable=True)
    op.create_foreign_key(
        "fk_document_shares_file_id_document_files",
        "document_shares",
        "document_files",
        ["file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_document_shares_file_id"), "document_shares", ["file_id"])
    op.create_check_constraint(
        "ck_document_shares_target",
        "document_shares",
        "(document_id IS NOT NULL) <> (file_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_document_shares_target", "document_shares", type_="check")
    op.execute("DELETE FROM document_shares WHERE file_id IS NOT NULL OR email IS NULL")
    op.drop_index(op.f("ix_document_shares_file_id"), table_name="document_shares")
    op.drop_constraint("fk_document_shares_file_id_document_files", "document_shares", "foreignkey")
    op.alter_column("document_shares", "email", existing_type=sa.String(255), nullable=False)
    op.alter_column("document_shares", "document_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("document_shares", "file_id")

    op.drop_column("signature_signers", "link_copies")
    op.drop_column("signature_signers", "executed_emailed_at")
    op.drop_column("signature_signers", "viewed_at")
    op.drop_column("signature_signers", "notified_at")
    op.drop_column("signature_signers", "token_version")

    op.drop_index(op.f("ix_signature_requests_executed_file_id"), table_name="signature_requests")
    op.drop_constraint(
        "fk_signature_requests_executed_file_id_document_files", "signature_requests", "foreignkey"
    )
    op.drop_column("signature_requests", "owner_emailed_at")
    op.drop_column("signature_requests", "executed_hash")
    op.drop_column("signature_requests", "executed_file_id")
    op.drop_column("signature_requests", "document_hash")
    op.drop_column("signature_requests", "sequential")
    op.drop_column("signature_requests", "message")
