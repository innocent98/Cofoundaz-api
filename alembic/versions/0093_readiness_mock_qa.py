"""Investor Readiness: mock Q&A sessions

Revision ID: 0093_readiness_mock_qa
Revises: 0092_marketplace_providers
Create Date: 2026-11-06

Slice D of Investor Readiness (PRD 15.3). Adds `mock_sessions`. Downgrade drops the table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0093_readiness_mock_qa"
down_revision = "0092_marketplace_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mock_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("focus", sa.String(20), nullable=False),
        sa.Column("intensity", sa.String(24), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error", sa.String(40), nullable=True),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("transcript", postgresql.JSONB(), nullable=False),
        sa.Column("scorecard", postgresql.JSONB(), nullable=True),
        sa.Column("rating", sa.String(12), nullable=True),
        sa.Column("average", sa.Float(), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_mock_sessions_startup_started", "mock_sessions", ["startup_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mock_sessions_startup_started", table_name="mock_sessions")
    op.drop_table("mock_sessions")
