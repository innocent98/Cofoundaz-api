"""campaigns_segments

Revision ID: 0034_campaigns_segments
Revises: 0033_marketing_calendar_channels
Create Date: 2026-09-24

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0034_campaigns_segments"
down_revision = "0033_marketing_calendar_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audience_segments",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("est_size", sa.Integer(), nullable=True),
        sa.Column("persona_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_audience_segments_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["business_records.id"],
            name=op.f("fk_audience_segments_persona_id_business_records"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audience_segments")),
    )
    op.create_index(
        op.f("ix_audience_segments_startup_id"), "audience_segments", ["startup_id"], unique=False
    )
    op.create_index(
        op.f("ix_audience_segments_persona_id"), "audience_segments", ["persona_id"], unique=False
    )
    op.create_table(
        "campaigns",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "objective",
            sa.Enum("awareness", "leads", "sales", "launch", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("budget", sa.Integer(), server_default="0", nullable=False),
        sa.Column("channel_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "active", "paused", "completed", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_campaigns_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(op.f("ix_campaigns_startup_id"), "campaigns", ["startup_id"], unique=False)
    op.create_table(
        "campaign_segments",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_segments_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["audience_segments.id"],
            name=op.f("fk_campaign_segments_segment_id_audience_segments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_segments")),
        sa.UniqueConstraint("campaign_id", "segment_id", name="uq_campaign_segment"),
    )
    op.create_index(
        op.f("ix_campaign_segments_campaign_id"),
        "campaign_segments",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_segments_segment_id"), "campaign_segments", ["segment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_campaign_segments_segment_id"), table_name="campaign_segments")
    op.drop_index(op.f("ix_campaign_segments_campaign_id"), table_name="campaign_segments")
    op.drop_table("campaign_segments")
    op.drop_index(op.f("ix_campaigns_startup_id"), table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index(op.f("ix_audience_segments_persona_id"), table_name="audience_segments")
    op.drop_index(op.f("ix_audience_segments_startup_id"), table_name="audience_segments")
    op.drop_table("audience_segments")
