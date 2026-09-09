import uuid
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import DocumentKind, DocumentStatus


class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[DocumentKind] = mapped_column(
        Enum(DocumentKind, native_enum=False, length=20),
        nullable=False,
        server_default=DocumentKind.custom.value,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=20),
        nullable=False,
        server_default=DocumentStatus.draft.value,
    )
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    folder: Mapped[str | None] = mapped_column(String(120), nullable=True)
    template_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    sections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (Index("ix_documents_startup_kind", "startup_id", "kind"),)
