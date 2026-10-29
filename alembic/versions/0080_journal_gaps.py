"""Journal: settings (passcode, reminders), retros, prompt dismissal

Revision ID: 0080_journal_gaps
Revises: 0079_calendar_meetings_sync
Create Date: 2026-10-29

journal_settings (per founder: optional passcode hash, reminder toggles, supportive-card
dismissal), journal_retros (encrypted milestone retrospectives, offer dismissals) and
journal_prompts.dismissed. Downgrade drops them.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0080_journal_gaps"
down_revision = "0079_calendar_meetings_sync"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "founder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]


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
    op.add_column(
        "journal_prompts",
        sa.Column("dismissed", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "journal_settings",
        *_base(),
        sa.Column("passcode_hash", sa.String(100), nullable=True),
        sa.Column("passcode_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "daily_prompt_notifications", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("evening_reminder", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("supportive_dismissed_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("startup_id", "founder_id", name="uq_journal_settings_startup_founder"),
    )
    op.create_table(
        "journal_retros",
        *_base(),
        sa.Column(
            "milestone_id",
            UUID(as_uuid=True),
            sa.ForeignKey("roadmap_milestones.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("milestone_title", sa.String(200), nullable=False),
        sa.Column("content_encrypted", sa.Text(), nullable=True),
        sa.Column("dismissed", sa.Boolean(), server_default="false", nullable=False),
        *_stamps(),
        sa.UniqueConstraint(
            "founder_id", "milestone_id", name="uq_journal_retros_founder_milestone"
        ),
    )


def downgrade() -> None:
    op.drop_table("journal_retros")
    op.drop_table("journal_settings")
    op.drop_column("journal_prompts", "dismissed")
