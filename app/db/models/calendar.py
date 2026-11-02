import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CalendarEventType


class CalendarEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "calendar_events"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL, not CASCADE: the event belongs to the workspace, not to whoever typed it, so a
    # departing user must not take the team's calendar with them.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[CalendarEventType] = mapped_column(
        # values_callable so the DB stores the enum *value*, matching the migration labels and the
        # lowercase strings the FE badges.
        Enum(
            CalendarEventType,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Naive wall-clock (spec D6). There is no timezone field anywhere in the app, so a time is
    # stored exactly as sent and returned exactly as stored; nothing converts.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Multi-day events: NULL means "ends on event_date". Never earlier than event_date.
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # IANA zone the wall-clock times are in (D-CAL-5). Existing rows were backfilled to the
    # only market served today.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Africa/Lagos", server_default="Africa/Lagos"
    )
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False, default="team", server_default="team"
    )
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        Index("ix_calendar_events_startup_id_event_date", "startup_id", "event_date"),
    )


class CalendarAttendee(UUIDMixin, TimestampMixin, Base):
    """One invitee of a meeting: an internal member (user_id set) or an external email."""

    __tablename__ = "calendar_attendees"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Invite email bookkeeping: sent_sequence is the ICS SEQUENCE last mailed, so an update
    # re-invites and a repeated job never double-sends.
    invite_state: Mapped[str] = mapped_column(
        String(12), nullable=False, default="pending", server_default="pending"
    )
    invite_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=-1, server_default="-1"
    )

    __table_args__ = (
        UniqueConstraint("event_id", "email", name="uq_calendar_attendees_event_email"),
    )


class CalendarMeeting(UUIDMixin, TimestampMixin, Base):
    """The meeting-specific half of an event of type `meeting` (1:1)."""

    __tablename__ = "calendar_meetings"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    agenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D-CAL-1: no meeting bot. The user pastes notes or a transcript after the meeting.
    assistant_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decisions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # [{id, title, assignee_id, task_id}] - task_id is set once added to the roadmap.
    action_items: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    assistant_state: Mapped[str] = mapped_column(
        String(12), nullable=False, default="idle", server_default="idle"
    )
    assistant_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    summarised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalendarReminderRule(UUIDMixin, TimestampMixin, Base):
    """Per-workspace reminder offsets for one kind of event (PRD 19.4)."""

    __tablename__ = "calendar_reminder_rules"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Minutes before the start; sorted descending, deduplicated, 1..20160 (14 days).
    offsets_minutes: Mapped[list] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("startup_id", "kind", name="uq_calendar_reminder_rules_kind"),
    )


class CalendarConnection(UUIDMixin, TimestampMixin, Base):
    """One user's link to an external calendar (Google now, Microsoft later).

    Per USER, not per workspace (D-CAL-2): the imported events are private to this user.
    Tokens are Fernet-encrypted and never returned. A `pending` row is created by `connect`
    and holds the single-use OAuth nonce until the callback consumes it.
    """

    __tablename__ = "calendar_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(12), nullable=False)
    # pending | active | needs_attention | disconnected
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    oauth_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nonce_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    access_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[str | None] = mapped_column(String(500), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    calendar_id: Mapped[str] = mapped_column(
        String(200), nullable=False, default="primary", server_default="primary"
    )
    # two_way | pull_only | push_only
    direction: Mapped[str] = mapped_column(
        String(12), nullable=False, default="two_way", server_default="two_way"
    )
    sync_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_calendar_connections_user_provider"),
    )


class CalendarExternalEvent(UUIDMixin, TimestampMixin, Base):
    """An event pulled from the user's external calendar. Private to `connection.user_id`;
    teammates only ever see a busy block derived from `starts_at`/`ends_at`."""

    __tablename__ = "calendar_external_events"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_calendar_external_events_ext"),
        Index("ix_calendar_external_events_conn_start", "connection_id", "starts_at"),
    )


class CalendarEventLink(UUIDMixin, TimestampMixin, Base):
    """Maps a Cofoundaz event to the copy pushed to one user's external calendar."""

    __tablename__ = "calendar_event_links"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # SHA-256 of the body last pushed: any later edit changes it and makes the link stale.
    pushed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", "connection_id", name="uq_calendar_event_links_pair"),
    )
