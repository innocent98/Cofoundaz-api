import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class Comment(UUIDMixin, TimestampMixin, Base):
    """A comment on any workspace entity (PRD 23.2 thread component; entities: roadmap
    milestones and tasks, Business Builder canvases (per block via `anchor`) and records). `entity_id` is a soft reference validated on write, so threads
    survive regardless of which module owns the entity."""

    __tablename__ = "comments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # A section inside the entity (Business Builder canvas block key); None = the whole entity.
    anchor: Mapped[str | None] = mapped_column(String(60), nullable=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mention_user_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_comments_entity", "startup_id", "entity_type", "entity_id"),)
