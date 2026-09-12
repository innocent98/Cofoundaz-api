import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.document import Document, DocumentShare
from app.db.models.enums import ShareAccess
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token


def create_share(
    db: Session,
    document: Document,
    *,
    shared_by_id: uuid.UUID,
    email: str,
    access_level: ShareAccess,
    expires_in_days: int | None,
) -> tuple[DocumentShare, str]:
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
    row = DocumentShare(
        startup_id=document.startup_id,
        document_id=document.id,
        shared_by_id=shared_by_id,
        email=email,
        access_level=access_level,
        token_hash=hash_token(raw),
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    event_bus.publish(
        "document.shared",
        {
            "startup_id": str(document.startup_id),
            "document_id": str(document.id),
            "share_id": str(row.id),
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


def list_workspace_shares(db: Session, startup: Startup) -> list[DocumentShare]:
    return (
        db.query(DocumentShare)
        .filter_by(startup_id=startup.id)
        .order_by(DocumentShare.created_at.desc())
        .all()
    )


def revoke_share(db: Session, share: DocumentShare) -> None:
    share.revoked_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(
        "document.share.revoked",
        {"startup_id": str(share.startup_id), "share_id": str(share.id)},
    )


def _is_expired(share: DocumentShare) -> bool:
    return share.expires_at is not None and share.expires_at <= datetime.now(UTC)


def open_shared(db: Session, token: str) -> DocumentShare:
    share = db.query(DocumentShare).filter_by(token_hash=hash_token(token)).first()
    # Uniform 404 for unknown / revoked / expired — don't leak whether a token existed.
    if share is None or share.revoked_at is not None or _is_expired(share):
        raise NotFound()
    share.last_viewed_at = datetime.now(UTC)
    db.flush()
    return share


def share_document(db: Session, membership: Membership, document_id: Any) -> Document:
    doc = db.query(Document).filter_by(id=document_id, startup_id=membership.startup_id).first()
    if doc is None:
        raise NotFound()
    return doc


def _share(db: Session, membership: Membership, document_id: Any, share_id: Any) -> DocumentShare:
    row = (
        db.query(DocumentShare)
        .filter_by(id=share_id, document_id=document_id, startup_id=membership.startup_id)
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


def serialize_share(share: DocumentShare, *, with_document: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(share.id),
        "email": share.email,
        "access_level": share.access_level.value,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
        "last_viewed_at": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "created_at": share.created_at.isoformat(),
        "status": _status(share),
    }
    if with_document:
        out["document_id"] = str(share.document_id)
    return out
