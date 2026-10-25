import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    DocumentKind,
    DocumentStatus,
    ScanStatus,
    ShareAccess,
    SignatureRequestStatus,
)


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
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    # Plain heading + body text of `sections`, maintained on every write; what library search reads.
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Soft delete: a trashed document is invisible everywhere (library, grantees, token links,
    # search) until restored, and is purged for good TRASH_RETENTION_DAYS after this stamp.
    trashed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trashed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("ix_documents_startup_kind", "startup_id", "kind"),)


class DocumentFile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "document_files"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    folder: Mapped[str | None] = mapped_column(String(120), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # Legacy public URL of a file uploaded before documents went private. NULL once the file is
    # private; nothing serves it (reads go through a short-lived signed link).
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # True once the stored object is an authenticated asset with no public URL. New uploads are
    # private from the start; `files_backfill` moves the legacy ones.
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    scan_status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, native_enum=False, length=20),
        nullable=False,
        server_default=ScanStatus.pending.value,
    )
    scanned_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    trashed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trashed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Set on a PDF/DOCX export of a rich document: which document, at which version. NULL for an
    # upload. The file outlives its source (SET NULL when the document is purged).
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_document_files_startup_folder", "startup_id", "folder"),)


class DocumentShare(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "document_shares"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shared_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    access_level: Mapped[ShareAccess] = mapped_column(
        Enum(ShareAccess, native_enum=False, length=20), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_viewed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_document_shares_startup_created", "startup_id", "created_at"),)


class SignatureRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "signature_requests"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # NO ACTION (not CASCADE): deleting a file must never take its signature requests, least
        # of all a completed one, with it. Workspace deletion still works because both tables
        # cascade from `startups` in the same statement.
        ForeignKey("document_files.id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SignatureRequestStatus] = mapped_column(
        Enum(SignatureRequestStatus, native_enum=False, length=20),
        nullable=False,
        server_default=SignatureRequestStatus.awaiting.value,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_signature_requests_startup_created", "startup_id", "created_at"),)


class SignatureSigner(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "signature_signers"

    request_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("signature_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    signed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signed_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class DocumentVersion(UUIDMixin, TimestampMixin, Base):
    """An immutable snapshot of a document at one version number: written on create, on every
    save (`PUT`) and on restore (which makes a NEW version holding the old content)."""

    __tablename__ = "document_versions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=20),
        nullable=False,
        server_default=DocumentStatus.draft.value,
    )
    sections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_versions_doc"),)


class DocumentFolder(UUIDMixin, TimestampMixin, Base):
    """A named folder, optionally nested. Documents and files keep their `folder` as the
    folder's full `path` ("Legal/NDAs"), so the existing `folder` filter and field are unchanged;
    this table is what makes empty folders, renames and counts possible."""

    __tablename__ = "document_folders"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_folders.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    path: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (UniqueConstraint("startup_id", "path", name="uq_document_folders_path"),)


class DocumentGrant(UUIDMixin, TimestampMixin, Base):
    """D-DOC-1: a per-person grant on one document. The grantee is an account (`grantee_user_id`)
    or, until they have a verified account, only an email (`grantee_email`, lower-cased) that
    binds to the account whose verified email matches. `revoked_at` keeps the row as the record
    of who had access."""

    __tablename__ = "document_grants"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grantee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    grantee_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    permission: Mapped[ShareAccess] = mapped_column(
        Enum(ShareAccess, native_enum=False, length=20), nullable=False
    )
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        # One live grant per person per document; revoked rows pile up as history.
        Index(
            "uq_document_grants_active",
            "document_id",
            "grantee_email",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
