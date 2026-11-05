"""Investor Readiness: score, history, market-size inputs

Revision ID: 0089_readiness_scores
Revises: 0088_content_reports
Create Date: 2026-11-05

Slice A of Investor Readiness (PRD 15.1 and 15.5). Adds `readiness_scores` (latest row per
workspace), `readiness_score_history` (a row per change) and `readiness_inputs` (founder-entered
market size). Downgrade drops all three.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0089_readiness_scores"
down_revision = "0088_content_reports"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _startup(unique: bool = False) -> sa.Column:
    return sa.Column(
        "startup_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=unique,
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "readiness_scores",
        _id(),
        _startup(),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(20), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("must_haves", postgresql.JSONB(), nullable=False),
        sa.Column("gaps", postgresql.JSONB(), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_score", sa.Integer(), nullable=True),
        *_stamps(),
    )
    op.create_index(
        "ix_readiness_scores_startup_id", "readiness_scores", ["startup_id"], unique=True
    )
    op.create_table(
        "readiness_score_history",
        _id(),
        _startup(),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(20), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dimension_scores", postgresql.JSONB(), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        *_stamps(),
    )
    op.create_index(
        "ix_readiness_history_startup_created",
        "readiness_score_history",
        ["startup_id", "created_at"],
    )
    op.create_table(
        "readiness_inputs",
        _id(),
        _startup(unique=True),
        sa.Column("tam_minor", sa.BigInteger(), nullable=True),
        sa.Column("sam_minor", sa.BigInteger(), nullable=True),
        sa.Column("som_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_stamps(),
    )


def downgrade() -> None:
    op.drop_table("readiness_inputs")
    op.drop_index("ix_readiness_history_startup_created", table_name="readiness_score_history")
    op.drop_table("readiness_score_history")
    op.drop_index("ix_readiness_scores_startup_id", table_name="readiness_scores")
    op.drop_table("readiness_scores")
