"""marketing metrics

Revision ID: 0038_marketing_metrics
Revises: 0037_seo_tools
Create Date: 2026-09-29

Module 10 Slice 5 (Performance Analytics): one new table marketing_metrics — a per-startup
time-series of (ts, channel?, campaign_id?, metric, value) points powering the analytics
aggregation. Additive; no lock on existing tables. New MarketingMetricName enum stored inline
(native_enum=False).
"""

from alembic import op
import sqlalchemy as sa

revision = "0038_marketing_metrics"
down_revision = "0037_seo_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_metrics",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("organic_social", "paid_social", "search", "email", "content_seo",
                    "partnerships", "events", "referral",
                    name="channelkey", native_enum=False, length=20),
            nullable=True,
        ),
        sa.Column("campaign_id", sa.UUID(), nullable=True),
        sa.Column(
            "metric",
            sa.Enum("visits", "impressions", "clicks", "conversions", "spend",
                    name="marketingmetricname", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_marketing_metrics_startup_id_startups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name=op.f("fk_marketing_metrics_campaign_id_campaigns"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_metrics")),
    )
    op.create_index(op.f("ix_marketing_metrics_startup_id"), "marketing_metrics", ["startup_id"], unique=False)
    op.create_index(op.f("ix_marketing_metrics_campaign_id"), "marketing_metrics", ["campaign_id"], unique=False)
    op.create_index("ix_marketing_metrics_startup_ts", "marketing_metrics", ["startup_id", "ts"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_marketing_metrics_startup_ts", table_name="marketing_metrics")
    op.drop_index(op.f("ix_marketing_metrics_campaign_id"), table_name="marketing_metrics")
    op.drop_index(op.f("ix_marketing_metrics_startup_id"), table_name="marketing_metrics")
    op.drop_table("marketing_metrics")
