"""Documents BE-C: exports record the document they were made from

Revision ID: 0074_documents_templates_export
Revises: 0073_documents_library_core
Create Date: 2026-10-25

document_files gains `source_document_id` (FK documents, SET NULL: an export outlives a purged
source) and `source_version`. NULL on both means an ordinary upload. The new legal DocumentKind
values need no DDL (`documents.kind` is a plain VARCHAR(20) with no CHECK constraint).

Downgrade drops the two columns (exports become indistinguishable from uploads).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0074_documents_templates_export"
down_revision = "0073_documents_library_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_files",
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("document_files", sa.Column("source_version", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_document_files_source_document_id_documents",
        "document_files",
        "documents",
        ["source_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_document_files_source_document_id"), "document_files", ["source_document_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_document_files_source_document_id"), table_name="document_files")
    op.drop_constraint(
        "fk_document_files_source_document_id_documents", "document_files", type_="foreignkey"
    )
    op.drop_column("document_files", "source_version")
    op.drop_column("document_files", "source_document_id")
