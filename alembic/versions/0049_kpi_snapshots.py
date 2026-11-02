"""kpi snapshots

Revision ID: 0049_kpi_snapshots
Revises: 0048_briefing_structured
Create Date: 2026-10-17

PRD 02.1 `kpi_snapshots(startup_id, metric, value, period, captured_at)`: one row per workspace
per metric per day, written by the nightly `scheduled.kpi.snapshot` job. A brand-new table, so
only a brief lock to validate the FK. The FK is indexed (house rule), and a unique
(startup_id, metric, period) makes the nightly capture idempotent.
"""

import sqlalchemy as sa

from alembic import op

revision = "0049_kpi_snapshots"
down_revision = "0048_briefing_structured"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kpi_snapshots",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("startup_id", "metric", "period", name="uq_kpi_snapshot_metric_period"),
    )
    op.create_index(
        op.f("ix_kpi_snapshots_startup_id"), "kpi_snapshots", ["startup_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_kpi_snapshots_startup_id"), table_name="kpi_snapshots")
    op.drop_table("kpi_snapshots")
