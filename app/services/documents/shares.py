import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.document import Document, DocumentFile, DocumentShare
from app.db.models.enums import ScanStatus, ShareAccess
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.documents.common import display_name
from app.services.documents.files import assert_clean
from app.services.documents.links import derive_token, matches, plausible, token_hash

MAX_EXPIRY_DAYS = 365
_MIN_LEAD = timedelta(minutes=1)


def _bad_expiry(message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        message,
        422,
        field_errors=[{"field": "expires_at", "message": message}],
    )


def resolve_expiry(
    *, expires_at: datetime | None, expires_in_days: int | None, now: datetime | None = None
) -> datetime | None:
    """The expiry instant: an explicit date wins over a day count; neither means never. A date must
    be in the future and within a year; a naive datetime is read as UTC."""
    now = now or datetime.now(UTC)
    if expires_at is not None:
        when = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        when = when.astimezone(UTC)
        if when <= now + _MIN_LEAD:
            raise _bad_expiry("The expiry date must be in the future.")
        if when > now + timedelta(days=MAX_EXPIRY_DAYS):
            raise _bad_expiry(f"A link can't last more than {MAX_EXPIRY_DAYS} days.")
        return when
    if expires_in_days:
        if expires_in_days < 0 or expires_in_days > MAX_EXPIRY_DAYS:
            raise _bad_expiry(f"A link can't last more than {MAX_EXPIRY_DAYS} days.")
        return now + timedelta(days=expires_in_days)
    return None


def share_token(share: DocumentShare) -> str:
    return derive_token("share", share.id)


def create_share(
    db: Session,
    target: Document | DocumentFile,
    *,
    shared_by_id: uuid.UUID,
    email: str | None,
    access_level: ShareAccess,
    expires_at: datetime | None = None,
    expires_in_days: int | None = None,
) -> tuple[DocumentShare, str]:
    """A link share of a rich document or a file. `email=None` is "anyone with the link": nothing is
    emailed and anyone holding the link can open it. A file is view-only and must have passed the
    scan. The returned token is recomputable later (`share_token`); only its hash is stored."""
    if expires_at is None and expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
    share_id = uuid.uuid4()
    raw = derive_token("share", share_id)
    is_file = isinstance(target, DocumentFile)
    if is_file:
        assert_clean(target)  # type: ignore[arg-type]
        access_level = ShareAccess.view
    row = DocumentShare(
        id=share_id,
        startup_id=target.startup_id,
        document_id=None if is_file else target.id,
        file_id=target.id if is_file else None,
        shared_by_id=shared_by_id,
        email=email,
        access_level=access_level,
        token_hash=token_hash(raw),
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    if isinstance(target, Document):
        event_bus.publish(
            db,
            "document.shared",
            {
                "startup_id": str(target.startup_id),
                "document_id": str(target.id),
                "share_id": str(row.id),
                "shared_by_id": str(shared_by_id),
                "owner_id": str(target.created_by_id) if target.created_by_id else None,
                "title": target.title,
                "sharer_name": display_name(db, shared_by_id),
                "href": f"/documents/{target.id}",
            },
        )
    return row, raw


def list_shares(db: Session, document: Document) -> list[DocumentShare]:
    return (
        db.query(DocumentShare)
        .filter_by(document_id=document.id)
        .order_by(DocumentShare.created_at.desc())
        .all()
    )


def list_file_shares(db: Session, file: DocumentFile) -> list[DocumentShare]:
    return (
        db.query(DocumentShare)
        .filter_by(file_id=file.id)
        .order_by(DocumentShare.created_at.desc())
        .all()
    )


def list_workspace_shares(db: Session, startup: Startup) -> list[DocumentShare]:
    docs = (
        db.query(DocumentShare)
        .join(Document, Document.id == DocumentShare.document_id)
        .filter(DocumentShare.startup_id == startup.id, Document.trashed_at.is_(None))
    )
    files = (
        db.query(DocumentShare)
        .join(DocumentFile, DocumentFile.id == DocumentShare.file_id)
        .filter(DocumentShare.startup_id == startup.id, DocumentFile.trashed_at.is_(None))
    )
    rows = docs.all() + files.all()
    rows.sort(key=lambda r: r.created_at, reverse=True)
    return rows


def revoke_share(db: Session, share: DocumentShare) -> None:
    share.revoked_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(
        db,
        "document.share.revoked",
        {"startup_id": str(share.startup_id), "share_id": str(share.id)},
    )


def set_expiry(db: Session, share: DocumentShare, expires_at: datetime | None) -> DocumentShare:
    """Change a live share's expiry (None = never). A revoked share stays revoked."""
    if share.revoked_at is not None:
        raise NotFound()
    share.expires_at = expires_at
    db.flush()
    return share


def _is_expired(share: DocumentShare) -> bool:
    return share.expires_at is not None and share.expires_at <= datetime.now(UTC)


def open_shared(db: Session, token: str) -> DocumentShare:
    if not plausible(token):
        raise NotFound()
    share = db.query(DocumentShare).filter_by(token_hash=token_hash(token)).first()
    # Uniform 404 for unknown / revoked / expired / trashed-or-blocked target: don't leak whether
    # a token ever existed.
    if (
        share is None
        or not matches(share.token_hash, token)
        or share.revoked_at is not None
        or _is_expired(share)
    ):
        raise NotFound()
    if share.document_id is not None:
        live = (
            db.query(Document.id)
            .filter(Document.id == share.document_id, Document.trashed_at.is_(None))
            .first()
        )
    else:
        live = (
            db.query(DocumentFile.id)
            .filter(
                DocumentFile.id == share.file_id,
                DocumentFile.trashed_at.is_(None),
                DocumentFile.scan_status == ScanStatus.clean,
            )
            .first()
        )
    if live is None:
        raise NotFound()
    share.last_viewed_at = datetime.now(UTC)
    db.flush()
    return share


def share_document(db: Session, membership: Membership, document_id: Any) -> Document:
    doc = (
        db.query(Document)
        .filter_by(id=document_id, startup_id=membership.startup_id)
        .filter(Document.trashed_at.is_(None))
        .first()
    )
    if doc is None:
        raise NotFound()
    return doc


def _share(db: Session, membership: Membership, target_id: Any, share_id: Any) -> DocumentShare:
    """A share of this document OR file (the id is unique across both tables' uuid space)."""
    row = (
        db.query(DocumentShare)
        .filter_by(id=share_id, startup_id=membership.startup_id)
        .filter((DocumentShare.document_id == target_id) | (DocumentShare.file_id == target_id))
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def _status(share: DocumentShare) -> str:
    if share.revoked_at is not None:
        return "revoked"
    if _is_expired(share):
        return "expired"
    return "active"


def share_link_if_current(share: DocumentShare) -> str | None:
    """The raw token of a live share made with the derived scheme, else None (a pre-v2 share has
    a random token that can't be recomputed)."""
    raw = share_token(share)
    return raw if matches(share.token_hash, raw) and _status(share) == "active" else None


def serialize_share(
    share: DocumentShare, *, with_document: bool = False, link_base: str | None = None
) -> dict[str, Any]:
    """`link_base` (the FE origin) is given only to editors: the share link is returned to them
    and to no one else."""
    token = share_link_if_current(share) if link_base is not None else None
    out: dict[str, Any] = {
        "id": str(share.id),
        "kind": "file" if share.file_id is not None else "document",
        "email": share.email,
        "anyone_with_link": share.email is None,
        "access_level": share.access_level.value,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
        "last_viewed_at": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "created_at": share.created_at.isoformat(),
        "status": _status(share),
    }
    if link_base is not None:
        out["link"] = f"{link_base}/shared/{token}" if token else None
    if with_document:
        out["document_id"] = str(share.document_id) if share.document_id else None
        out["file_id"] = str(share.file_id) if share.file_id else None
    return out
