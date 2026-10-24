"""Documents: file scan status; signature requests no longer cascade from files

Revision ID: 0072_documents_scan_status
Revises: 0071_finance_slice_e
Create Date: 2026-10-24

1. document_files gains `scan_status` (pending / clean / blocked) and `scanned_at`. Files that
   already exist were stored before any scan hook or magic-byte check existed; they are
   grandfathered to `clean` here so live signature requests keep working. New uploads start
   `pending` and the scanner hook decides (see app/services/documents/scanner.py).
2. signature_requests.file_id loses ON DELETE CASCADE (it becomes NO ACTION): deleting a file used
   to delete its signature requests, including completed ones. The API now refuses (409) and the
   database backs that up. Workspace deletion still works: both tables cascade from `startups`.

Downgrade restores the CASCADE and drops the scan columns.
"""

import sqlalchemy as sa

from alembic import op

revision = "0072_documents_scan_status"
down_revision = "0071_finance_slice_e"
branch_labels = None
depends_on = None

_FK = "fk_signature_requests_file_id_document_files"


def upgrade() -> None:
    op.add_column(
        "document_files",
        sa.Column("scan_status", sa.String(length=20), server_default="pending", nullable=False),
    )
    op.add_column(
        "document_files", sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE document_files SET scan_status = 'clean', scanned_at = now()"
    )  # grandfather existing rows
    op.drop_constraint(_FK, "signature_requests", type_="foreignkey")
    op.create_foreign_key(_FK, "signature_requests", "document_files", ["file_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(_FK, "signature_requests", type_="foreignkey")
    op.create_foreign_key(
        _FK, "signature_requests", "document_files", ["file_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_column("document_files", "scanned_at")
    op.drop_column("document_files", "scan_status")
