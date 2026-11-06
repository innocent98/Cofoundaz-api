"""Investor Readiness: financial narrative draft and checks

Revision ID: 0103_readiness_narrative
Revises: 0099_funding_cap_table
Create Date: 2026-11-06

Slice C of Investor Readiness (PRD 15.4). Adds `readiness_narrative` (one draft per workspace)
and `narrative_checks` (history of "check my narrative against my numbers" runs). Downgrade
drops both tables.

CHAIN NOTE: 0101 (Marketplace C) and 0102 (Funding D) are reserved; re-chain onto whichever
lands first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0103_readiness_narrative"
down_revision = "0099_funding_cap_table"
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "readiness_narrative",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "narrative_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deck_review_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deck_reviews.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("draft_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error", sa.String(40), nullable=True),
        sa.Column("claims", postgresql.JSONB(), nullable=True),
        sa.Column("use_of_funds", postgresql.JSONB(), nullable=True),
        sa.Column("facts", postgresql.JSONB(), nullable=True),
        sa.Column("findings", postgresql.JSONB(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_narrative_checks_startup_created", "narrative_checks", ["startup_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_narrative_checks_startup_created", table_name="narrative_checks")
    op.drop_table("narrative_checks")
    op.drop_table("readiness_narrative")
