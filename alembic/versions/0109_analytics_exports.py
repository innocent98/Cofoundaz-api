"""Analytics & Reports slice C: exports

Revision ID: 0109_analytics_exports
Revises: 0108_analytics_reports
Create Date: 2026-11-13

`analytics_exports`: one row per exported file (report PDF/XLSX, data-tables XLSX/CSV), rendered
from a frozen run. The file is in private storage under `file_key`; `hubs` records whose numbers
are in it (the download re-checks them); `expires_at` is creation + 30 days and the daily purge
sets `purged_at` when the file is gone. Downgrade drops the table (the stored files are left for
the storage backend; their keys are under `analytics/exports/`).

CHAIN NOTE: re-check `alembic heads` before merging; re-chain onto whichever migration lands first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0109_analytics_exports"
down_revision = "0108_analytics_reports"
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _uuid_fk(name: str, target: str, ondelete: str, nullable: bool) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "analytics_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("startup_id", "startups.id", "CASCADE", False),
        _uuid_fk("run_id", "analytics_report_runs.id", "SET NULL", True),
        _uuid_fk("report_id", "analytics_reports.id", "SET NULL", True),
        sa.Column("artifact_type", sa.String(20), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("file_key", sa.String(300), nullable=True),
        sa.Column("filename", sa.String(200), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("hubs", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("options_json", postgresql.JSONB(), server_default="{}", nullable=False),
        _uuid_fk("created_by", "users.id", "SET NULL", True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("download_count", sa.Integer(), server_default="0", nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_analytics_exports_run_id", "analytics_exports", ["run_id"])
    op.create_index(
        "ix_analytics_exports_startup_created", "analytics_exports", ["startup_id", "created_at"]
    )
    op.create_index("ix_analytics_exports_expires", "analytics_exports", ["expires_at"])


def downgrade() -> None:
    op.drop_table("analytics_exports")
