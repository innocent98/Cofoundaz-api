"""comments

Revision ID: 0051_comments
Revises: 0050_roadmap_task_mission_rank
Create Date: 2026-10-18

PRD 23.2 thread component: comments on any workspace entity (v1: roadmap milestones/tasks,
shown in the Roadmap drawer). A new table, so only a brief lock to validate the FKs. Every FK
is indexed (house rule) plus a composite (startup_id, entity_type, entity_id) for thread reads.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0051_comments"
down_revision = "0050_roadmap_task_mission_rank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "mention_user_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_comments_startup_id"), "comments", ["startup_id"], unique=False)
    op.create_index(op.f("ix_comments_author_id"), "comments", ["author_id"], unique=False)
    op.create_index(
        "ix_comments_entity", "comments", ["startup_id", "entity_type", "entity_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_comments_entity", table_name="comments")
    op.drop_index(op.f("ix_comments_author_id"), table_name="comments")
    op.drop_index(op.f("ix_comments_startup_id"), table_name="comments")
    op.drop_table("comments")
