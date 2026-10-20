"""Marketing PRD gap build (b): channel fit level, keyword update stamp, campaign readouts

Revision ID: 0062_marketing_prd_b
Revises: 0061_marketing_prd_a
Create Date: 2026-10-20

Rows 20 (fit_level), 22 (metrics_updated_at), 5 (campaign_readouts). All additive and nullable.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0062_marketing_prd_b"
down_revision = "0061_marketing_prd_a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketing_channels", sa.Column("fit_level", sa.String(length=10), nullable=True))
    op.add_column(
        "seo_keywords", sa.Column("metrics_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "campaign_readouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "week_start", name="uq_campaign_readout_week"),
    )
    op.create_index("ix_campaign_readouts_startup_id", "campaign_readouts", ["startup_id"])
    op.create_index("ix_campaign_readouts_campaign_id", "campaign_readouts", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_campaign_readouts_campaign_id", table_name="campaign_readouts")
    op.drop_index("ix_campaign_readouts_startup_id", table_name="campaign_readouts")
    op.drop_table("campaign_readouts")
    op.drop_column("seo_keywords", "metrics_updated_at")
    op.drop_column("marketing_channels", "fit_level")
