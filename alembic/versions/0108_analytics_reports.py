"""Analytics & Reports slice B: reports, shares and frozen runs

Revision ID: 0108_analytics_reports
Revises: 0106_legal
Create Date: 2026-11-13

`analytics_reports` (definition + optimistic-lock `version` + soft delete), `analytics_report_shares`
(Founder-granted read, one row per report and member) and `analytics_report_runs` (a frozen
snapshot per run; `report_id` goes null if the report row is ever hard-deleted). Downgrade drops
all three.

CHAIN NOTE: re-check `alembic heads` before merging; re-chain onto whichever migration lands first
(0104 Marketplace D and 0107 Funding grants are reserved by other branches).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0108_analytics_reports"
down_revision = "0106_legal"
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
        "analytics_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("startup_id", "startups.id", "CASCADE", False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("blocks_json", postgresql.JSONB(), nullable=False),
        sa.Column("default_range_json", postgresql.JSONB(), nullable=False),
        _uuid_fk("owner_id", "users.id", "SET NULL", True),
        sa.Column("template_key", sa.String(40), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_analytics_reports_startup_deleted", "analytics_reports", ["startup_id", "deleted_at"]
    )
    op.create_table(
        "analytics_report_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("report_id", "analytics_reports.id", "CASCADE", False),
        _uuid_fk("user_id", "users.id", "CASCADE", False),
        _uuid_fk("created_by", "users.id", "SET NULL", True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("report_id", "user_id", name="uq_analytics_report_share"),
    )
    op.create_index("ix_analytics_report_shares_report_id", "analytics_report_shares", ["report_id"])
    op.create_index("ix_analytics_report_shares_user_id", "analytics_report_shares", ["user_id"])
    op.create_table(
        "analytics_report_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("report_id", "analytics_reports.id", "SET NULL", True),
        _uuid_fk("startup_id", "startups.id", "CASCADE", False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("tz", sa.String(40), server_default="UTC", nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=True),
        sa.Column("access_json", postgresql.JSONB(), nullable=False),
        sa.Column("triggered_by", sa.String(20), nullable=False),
        _uuid_fk("run_by", "users.id", "SET NULL", True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_analytics_report_runs_report_id", "analytics_report_runs", ["report_id"])
    op.create_index(
        "ix_analytics_report_runs_startup", "analytics_report_runs", ["startup_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("analytics_report_runs")
    op.drop_table("analytics_report_shares")
    op.drop_table("analytics_reports")
