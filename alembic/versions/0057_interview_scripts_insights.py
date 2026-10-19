"""interview scripts + insight reports

Revision ID: 0057_interview_scripts_insights
Revises: 0056_smoke_test_pages
Create Date: 2026-10-19

Validation Hub slice (b): AI-drafted interview scripts the founder can edit (PRD 09.4), and the
Insight Synthesizer's stored reports with per-suggestion Apply (PRD 09 "Insight Synthesizer").
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0057_interview_scripts_insights"
down_revision = "0056_smoke_test_pages"
branch_labels = None
depends_on = None


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(
        name, postgresql.JSONB(astext_type=sa.Text()), server_default=default, nullable=False
    )


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
        "interview_scripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        _jsonb("assumption_ids", "[]"),
        _jsonb("questions", "[]"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interview_scripts_startup_id", "interview_scripts", ["startup_id"])
    op.create_table(
        "insight_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        _jsonb("inputs", "{}"),
        _jsonb("patterns", "[]"),
        _jsonb("contradictions", "[]"),
        _jsonb("suggestions", "[]"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_reports_startup_id", "insight_reports", ["startup_id"])


def downgrade() -> None:
    op.drop_index("ix_insight_reports_startup_id", table_name="insight_reports")
    op.drop_table("insight_reports")
    op.drop_index("ix_interview_scripts_startup_id", table_name="interview_scripts")
    op.drop_table("interview_scripts")
