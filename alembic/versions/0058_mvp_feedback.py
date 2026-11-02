"""MVP feedback: feedback items, clustering runs, themes

Revision ID: 0058_mvp_feedback
Revises: 0057_interview_scripts_insights
Create Date: 2026-10-19

Validation Hub slice (c), D-VAL-2/3: an MVP feedback inbox (interview quotes and open survey
answers mirrored in, plus manual entries), AI theme clustering runs and their themes, each of
which can be sent to the roadmap once.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0058_mvp_feedback"
down_revision = "0057_interview_scripts_insights"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "feedback_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("origin_type", sa.String(length=20), nullable=False),
        sa.Column("origin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_label", sa.String(length=255), server_default="", nullable=False),
        sa.Column("origin_key", sa.String(length=160), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("dismissed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_items_startup_id", "feedback_items", ["startup_id"])
    op.create_index(
        "uq_feedback_items_origin",
        "feedback_items",
        ["startup_id", "origin_key"],
        unique=True,
        postgresql_where=sa.text("origin_key IS NOT NULL"),
    )
    op.create_table(
        "feedback_cluster_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_cluster_runs_startup_id", "feedback_cluster_runs", ["startup_id"])
    op.create_table(
        "feedback_themes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("theme", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "item_ids", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("roadmap_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["run_id"], ["feedback_cluster_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["roadmap_task_id"], ["roadmap_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_themes_run_id", "feedback_themes", ["run_id"])
    op.create_index("ix_feedback_themes_startup_id", "feedback_themes", ["startup_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_themes_startup_id", table_name="feedback_themes")
    op.drop_index("ix_feedback_themes_run_id", table_name="feedback_themes")
    op.drop_table("feedback_themes")
    op.drop_index("ix_feedback_cluster_runs_startup_id", table_name="feedback_cluster_runs")
    op.drop_table("feedback_cluster_runs")
    op.drop_index("uq_feedback_items_origin", table_name="feedback_items")
    op.drop_index("ix_feedback_items_startup_id", table_name="feedback_items")
    op.drop_table("feedback_items")
