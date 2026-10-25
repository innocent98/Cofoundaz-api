import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    # Exactly one of `document_id` / `file_id` is set (CHECK): a share is of a rich document or of
    # an uploaded/exported file.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_files.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    shared_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # NULL = "anyone with the link" (no recipient, nothing emailed).
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    __table_args__ = (
        Index("ix_document_shares_startup_created", "startup_id", "created_at"),
        CheckConstraint(
            "(document_id IS NOT NULL) <> (file_id IS NOT NULL)", name="ck_document_shares_target"
        ),
    )


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
    # The sender's note to the signers (the default sentence is stored when none was typed).
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True: signers sign one after another in `position` order; the next link is released only
    # when the previous signer has signed.
    sequential: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # SHA-256 of the PDF at send time (NULL on requests sent before e-sign v2).
    document_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The executed PDF (the original pages plus the signature and audit-certificate pages): a
    # private DocumentFile, set by the worker after the last signature.
    executed_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    executed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the executed copy was emailed to the sender (per signer: `executed_emailed_at`).
    owner_emailed_at: Mapped[datetime.datetime | None] = mapped_column(
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
    # Bumped to rotate the signer's link (a reminder); the link is derived from (id, version).
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # When the link was released to the signer (immediately, or in sequential mode when the
    # previous signer signed). NULL = not their turn yet; no usable token exists for them.
    notified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    viewed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    executed_emailed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
