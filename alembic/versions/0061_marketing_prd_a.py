"""Marketing PRD gap build (a): post media, campaign links on posts and copy

Revision ID: 0061_marketing_prd_a
Revises: 0060_assessment_stage_at_time
Create Date: 2026-10-20

Rows 2/13 (marketing-scoped media), 8 (campaign_id on calendar entries and copy generations).
All additive and nullable: no back-fill, existing rows read as "no media" / "no campaign".
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0061_marketing_prd_a"
down_revision = "0060_assessment_stage_at_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_marketing_media_startup_id", "marketing_media", ["startup_id"])

    op.add_column(
        "content_calendar", sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "content_calendar", sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_content_calendar_media_id",
        "content_calendar",
        "marketing_media",
        ["media_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_content_calendar_campaign_id",
        "content_calendar",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_content_calendar_media_id", "content_calendar", ["media_id"])
    op.create_index("ix_content_calendar_campaign_id", "content_calendar", ["campaign_id"])

    op.add_column(
        "marketing_ai_generations",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_marketing_ai_generations_campaign_id",
        "marketing_ai_generations",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_marketing_ai_generations_campaign_id", "marketing_ai_generations", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_marketing_ai_generations_campaign_id", table_name="marketing_ai_generations")
    op.drop_constraint(
        "fk_marketing_ai_generations_campaign_id", "marketing_ai_generations", type_="foreignkey"
    )
    op.drop_column("marketing_ai_generations", "campaign_id")
    op.drop_index("ix_content_calendar_campaign_id", table_name="content_calendar")
    op.drop_index("ix_content_calendar_media_id", table_name="content_calendar")
    op.drop_constraint("fk_content_calendar_campaign_id", "content_calendar", type_="foreignkey")
    op.drop_constraint("fk_content_calendar_media_id", "content_calendar", type_="foreignkey")
    op.drop_column("content_calendar", "campaign_id")
    op.drop_column("content_calendar", "media_id")
    op.drop_index("ix_marketing_media_startup_id", table_name="marketing_media")
    op.drop_table("marketing_media")
