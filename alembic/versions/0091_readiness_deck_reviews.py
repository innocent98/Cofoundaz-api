"""Investor Readiness: pitch deck reviews

Revision ID: 0091_readiness_deck_reviews
Revises: 0090_funding_core
Create Date: 2026-11-06

Slice B of Investor Readiness (PRD 15.2). Adds `deck_reviews`. Chained on Funding's
0090_funding_core. Downgrade drops the table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0091_readiness_deck_reviews"
down_revision = "0090_funding_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error", sa.String(40), nullable=True),
        sa.Column("slide_count", sa.Integer(), nullable=True),
        sa.Column("scores", postgresql.JSONB(), nullable=True),
        sa.Column("top_fixes", postgresql.JSONB(), nullable=True),
        sa.Column("slides", postgresql.JSONB(), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("startup_id", "version", name="uq_deck_reviews_startup_version"),
    )
    op.create_index("ix_deck_reviews_startup_sha", "deck_reviews", ["startup_id", "content_sha256"])


def downgrade() -> None:
    op.drop_index("ix_deck_reviews_startup_sha", table_name="deck_reviews")
    op.drop_table("deck_reviews")
