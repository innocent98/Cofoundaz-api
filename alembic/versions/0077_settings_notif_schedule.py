"""Settings: notification digest mode and quiet hours

Revision ID: 0077_settings_notif_schedule
Revises: 0076_documents_signer_link_audit
Create Date: 2026-10-27

notification_preferences gains digest_mode (realtime/daily/weekly), digest_time, digest_day
(0 = Monday), quiet_enabled, quiet_from, quiet_until and timezone. Storage only: nothing sends a
digest yet. Downgrade drops the columns.
"""

import sqlalchemy as sa

from alembic import op

revision = "0077_settings_notif_schedule"
down_revision = "0076_documents_signer_link_audit"
branch_labels = None
depends_on = None

_TABLE = "notification_preferences"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("digest_mode", sa.String(10), server_default="realtime", nullable=False),
    )
    op.add_column(_TABLE, sa.Column("digest_time", sa.Time(), server_default="08:00", nullable=False))
    op.add_column(
        _TABLE, sa.Column("digest_day", sa.SmallInteger(), server_default="0", nullable=False)
    )
    op.add_column(
        _TABLE, sa.Column("quiet_enabled", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(_TABLE, sa.Column("quiet_from", sa.Time(), server_default="20:00", nullable=False))
    op.add_column(_TABLE, sa.Column("quiet_until", sa.Time(), server_default="06:00", nullable=False))
    op.add_column(
        _TABLE, sa.Column("timezone", sa.String(64), server_default="Africa/Lagos", nullable=False)
    )


def downgrade() -> None:
    for col in (
        "timezone",
        "quiet_until",
        "quiet_from",
        "quiet_enabled",
        "digest_day",
        "digest_time",
        "digest_mode",
    ):
        op.drop_column(_TABLE, col)
