"""Calendar: meetings, attendees, reminder rules, Google sync

Revision ID: 0077_calendar_meetings_sync
Revises: 0076_documents_signer_link_audit
Create Date: 2026-10-27

calendar_events gains end_date, timezone (backfilled Africa/Lagos), location, video_url,
visibility and source_ref. New tables: calendar_attendees, calendar_meetings,
calendar_reminder_rules, calendar_connections, calendar_external_events, calendar_event_links.
Downgrade drops the new tables and columns.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0077_calendar_meetings_sync"
down_revision = "0076_documents_signer_link_audit"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("end_date", sa.Date(), nullable=True))
    op.add_column(
        "calendar_events",
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Africa/Lagos"),
    )
    op.add_column("calendar_events", sa.Column("location", sa.String(300), nullable=True))
    op.add_column("calendar_events", sa.Column("video_url", sa.String(500), nullable=True))
    op.add_column(
        "calendar_events",
        sa.Column("visibility", sa.String(10), nullable=False, server_default="team"),
    )
    op.add_column("calendar_events", sa.Column("source_ref", sa.String(200), nullable=True))

    op.create_table(
        "calendar_attendees",
        *_base(),
        sa.Column("event_id", UUID, sa.ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invite_state", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("invite_sequence", sa.Integer(), nullable=False, server_default="-1"),
        sa.UniqueConstraint("event_id", "email", name="uq_calendar_attendees_event_email"),
    )
    op.create_index("ix_calendar_attendees_event_id", "calendar_attendees", ["event_id"])
    op.create_index("ix_calendar_attendees_user_id", "calendar_attendees", ["user_id"])

    op.create_table(
        "calendar_meetings",
        *_base(),
        sa.Column(
            "event_id", UUID, sa.ForeignKey("calendar_events.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("agenda", sa.Text(), nullable=True),
        sa.Column("assistant_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("decisions", JSONB, nullable=False, server_default="[]"),
        sa.Column("action_items", JSONB, nullable=False, server_default="[]"),
        sa.Column("assistant_state", sa.String(12), nullable=False, server_default="idle"),
        sa.Column("assistant_error", sa.String(300), nullable=True),
        sa.Column("summarised_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "calendar_reminder_rules",
        *_base(),
        sa.Column("startup_id", UUID, sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("offsets_minutes", JSONB, nullable=False),
        sa.UniqueConstraint("startup_id", "kind", name="uq_calendar_reminder_rules_kind"),
    )
    op.create_index("ix_calendar_reminder_rules_startup_id", "calendar_reminder_rules", ["startup_id"])

    op.create_table(
        "calendar_connections",
        *_base(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("startup_id", UUID, sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("oauth_nonce", sa.String(64), nullable=True),
        sa.Column("nonce_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_ciphertext", sa.Text(), nullable=True),
        sa.Column("refresh_ciphertext", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(500), nullable=True),
        sa.Column("account_email", sa.String(254), nullable=True),
        sa.Column("calendar_id", sa.String(200), nullable=False, server_default="primary"),
        sa.Column("direction", sa.String(12), nullable=False, server_default="two_way"),
        sa.Column("sync_token", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_summary", JSONB, nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.UniqueConstraint("user_id", "provider", name="uq_calendar_connections_user_provider"),
    )
    op.create_index("ix_calendar_connections_user_id", "calendar_connections", ["user_id"])
    op.create_index("ix_calendar_connections_startup_id", "calendar_connections", ["startup_id"])

    op.create_table(
        "calendar_external_events",
        *_base(),
        sa.Column(
            "connection_id", UUID, sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("etag", sa.String(200), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("connection_id", "external_id", name="uq_calendar_external_events_ext"),
    )
    op.create_index("ix_calendar_external_events_connection_id", "calendar_external_events", ["connection_id"])
    op.create_index(
        "ix_calendar_external_events_conn_start", "calendar_external_events", ["connection_id", "starts_at"]
    )

    op.create_table(
        "calendar_event_links",
        *_base(),
        sa.Column("event_id", UUID, sa.ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "connection_id", UUID, sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("etag", sa.String(200), nullable=True),
        sa.Column("pushed_hash", sa.String(64), nullable=True),
        sa.UniqueConstraint("event_id", "connection_id", name="uq_calendar_event_links_pair"),
    )
    op.create_index("ix_calendar_event_links_event_id", "calendar_event_links", ["event_id"])
    op.create_index("ix_calendar_event_links_connection_id", "calendar_event_links", ["connection_id"])


def downgrade() -> None:
    for table in (
        "calendar_event_links",
        "calendar_external_events",
        "calendar_connections",
        "calendar_reminder_rules",
        "calendar_meetings",
        "calendar_attendees",
    ):
        op.drop_table(table)
    for col in ("source_ref", "visibility", "video_url", "location", "timezone", "end_date"):
        op.drop_column("calendar_events", col)
