"""Notifications: archive, per-category in-app switches, digest bookkeeping

Revision ID: 0081_notifications_gaps
Revises: 0077_settings_notif_schedule
Create Date: 2026-10-29

notifications.archived_at (inbox archive / restore), notifications.hidden (in-app switched off), notification_preferences.in_app (in-app
switch per category) and notification_preferences.last_digest_at (the digest sender's cursor).
Downgrade drops them.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0081_notifications_gaps"
down_revision = "0077_settings_notif_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "notifications", sa.Column("hidden", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "notification_preferences",
        sa.Column("in_app", JSONB(), server_default="{}", nullable=False),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "last_digest_at")
    op.drop_column("notification_preferences", "in_app")
    op.drop_column("notifications", "hidden")
    op.drop_column("notifications", "archived_at")
