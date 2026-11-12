"""Analytics & Reports slice A: founder-entered custom metrics

Revision ID: 0105_analytics_custom_metrics
Revises: 0101_marketplace_booking
Create Date: 2026-11-12

D-ANA-2 (b). `custom_metrics` (one per workspace key) and `custom_metric_points` (one value per
month). The nightly catalog snapshot reuses the existing `kpi_snapshots` table, so no change there.
Downgrade drops both tables.

CHAIN NOTE: re-check `alembic heads` before pushing; re-chain onto whichever migration lands first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0105_analytics_custom_metrics"
down_revision = "0101_marketplace_booking"
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "custom_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("unit", sa.String(10), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("startup_id", "key", name="uq_custom_metric_key"),
    )
    op.create_index("ix_custom_metrics_startup_id", "custom_metrics", ["startup_id"])
    op.create_table(
        "custom_metric_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "metric_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_metrics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column(
            "entered_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("metric_id", "period", name="uq_custom_metric_point"),
    )
    op.create_index("ix_custom_metric_points_metric_id", "custom_metric_points", ["metric_id"])


def downgrade() -> None:
    op.drop_table("custom_metric_points")
    op.drop_table("custom_metrics")
