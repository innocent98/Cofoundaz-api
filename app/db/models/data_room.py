"""Funding Hub slice B (PRD 14.2): the data room. Items point into Documents (never copy a file);
shares are token links gated by an emailed one-time code (D-FUND-3); every view is logged.

Only the SHA-256 of a share token is stored. A code is stored as a keyed hash. Every row carries
`startup_id` and every query filters by it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


def _startup_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )


class DataRoomFolder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "data_room_folders"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_standard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_data_room_folders_startup", "startup_id", "position"),)


class DataRoomItem(UUIDMixin, TimestampMixin, Base):
    """A reference to a rich Document or an uploaded file (exactly one)."""

    __tablename__ = "data_room_items"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    folder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_room_folders.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_files.id", ondelete="CASCADE"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "(document_id IS NULL) <> (file_id IS NULL)", name="ck_data_room_item_one_target"
        ),
        Index("ix_data_room_items_folder", "folder_id", "position"),
        Index("ix_data_room_items_startup", "startup_id"),
        Index(
            "uq_data_room_item_doc",
            "folder_id",
            "document_id",
            unique=True,
            postgresql_where=text("document_id IS NOT NULL"),
        ),
        Index(
            "uq_data_room_item_file",
            "folder_id",
            "file_id",
            unique=True,
            postgresql_where=text("file_id IS NOT NULL"),
        ),
    )


class DataRoomShare(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "data_room_shares"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    round_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("funding_rounds.id", ondelete="SET NULL"), nullable=True
    )
    investor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("investors.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # stored lower-case
    scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)  # folder ids
    allow_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watermark: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Brute-force guard (per share): consecutive wrong codes lock the gate for a while.
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_data_room_share_token", "token_hash", unique=True),
        Index("ix_data_room_shares_startup", "startup_id", "created_at"),
        Index("ix_data_room_shares_investor", "investor_id"),
    )


class DataRoomChallenge(UUIDMixin, TimestampMixin, Base):
    """One emailed code. Valid once, for ten minutes, for a few tries."""

    __tablename__ = "data_room_challenges"

    share_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_room_shares.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_data_room_challenges_share", "share_id", "created_at"),)


class DataRoomView(UUIDMixin, TimestampMixin, Base):
    """The access log: one row per opened document. `duration_s` grows with the heartbeat."""

    __tablename__ = "data_room_views"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    share_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_room_shares.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_room_items.id", ondelete="SET NULL"), nullable=True
    )
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)  # survives removal
    viewer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_data_room_views_startup", "startup_id", "started_at"),
        Index("ix_data_room_views_share", "share_id", "started_at"),
    )
