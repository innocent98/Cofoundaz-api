import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound, SignatureNotActive
from app.db.models.document import DocumentFile, SignatureRequest, SignatureSigner
from app.db.models.enums import SignatureRequestStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token


def create_request(
    db: Session,
    file: DocumentFile,
    *,
    created_by_id: uuid.UUID,
    title: str,
    signers: list[dict[str, Any]],
    expires_in_days: int | None,
) -> tuple[SignatureRequest, list[tuple[SignatureSigner, str]]]:
    if not signers:
        raise AppError("VALIDATION_ERROR", "At least one signer is required.", 422)
    expires_at = datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
    req = SignatureRequest(
        startup_id=file.startup_id,
        file_id=file.id,
        title=title,
        created_by_id=created_by_id,
        expires_at=expires_at,
    )
    db.add(req)
    db.flush()
    pairs: list[tuple[SignatureSigner, str]] = []
    for position, item in enumerate(signers):
        raw = secrets.token_urlsafe(32)
        signer = SignatureSigner(
            request_id=req.id,
            email=item["email"],
            name=item.get("name"),
            token_hash=hash_token(raw),
            position=position,
        )
        db.add(signer)
        pairs.append((signer, raw))
    db.flush()
    event_bus.publish(
        db,
        "document.signature.requested",
        {"startup_id": str(req.startup_id), "request_id": str(req.id), "file_id": str(file.id)},
    )
    return req, pairs


def list_requests(db: Session, startup: Startup) -> list[SignatureRequest]:
    return (
        db.query(SignatureRequest)
        .filter_by(startup_id=startup.id)
        .order_by(SignatureRequest.created_at.desc())
        .all()
    )


def get_request(db: Session, membership: Membership, request_id: Any) -> SignatureRequest:
    row = (
        db.query(SignatureRequest)
        .filter_by(id=request_id, startup_id=membership.startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def _signers(db: Session, request: SignatureRequest) -> list[SignatureSigner]:
    return (
        db.query(SignatureSigner)
        .filter_by(request_id=request.id)
        .order_by(SignatureSigner.position.asc())
        .all()
    )


def unsigned_signers(db: Session, request: SignatureRequest) -> list[SignatureSigner]:
    return [s for s in _signers(db, request) if s.signed_at is None]


def reissue_unsigned(db: Session, request: SignatureRequest) -> list[tuple[SignatureSigner, str]]:
    """Rotate the token of every still-unsigned signer (raw tokens aren't stored,
    so remind can't resend the original link — it mints a fresh one and the old
    link stops working). 409 if the request is no longer active."""
    if not _active(request):
        raise SignatureNotActive()
    pairs: list[tuple[SignatureSigner, str]] = []
    for signer in unsigned_signers(db, request):
        raw = secrets.token_urlsafe(32)
        signer.token_hash = hash_token(raw)
        pairs.append((signer, raw))
    db.flush()
    return pairs


def cancel_request(db: Session, request: SignatureRequest) -> None:
    if request.status != SignatureRequestStatus.awaiting:
        raise SignatureNotActive()
    request.status = SignatureRequestStatus.cancelled
    request.cancelled_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(
        db,
        "document.signature.cancelled",
        {"startup_id": str(request.startup_id), "request_id": str(request.id)},
    )


def _is_expired(request: SignatureRequest) -> bool:
    return request.expires_at is not None and request.expires_at <= datetime.now(UTC)


def _active(request: SignatureRequest) -> bool:
    return request.status == SignatureRequestStatus.awaiting and not _is_expired(request)


def open_for_signing(db: Session, token: str) -> SignatureSigner:
    signer = db.query(SignatureSigner).filter_by(token_hash=hash_token(token)).first()
    # Uniform 404: unknown / already-signed / request not active (cancelled/complete/expired).
    if signer is None or signer.signed_at is not None:
        raise NotFound()
    request = db.query(SignatureRequest).filter_by(id=signer.request_id).one()
    if not _active(request):
        raise NotFound()
    return signer


def record_signature(
    db: Session,
    signer: SignatureSigner,
    *,
    typed_name: str,
    ip: str | None,
    user_agent: str | None,
) -> SignatureRequest:
    request = db.query(SignatureRequest).filter_by(id=signer.request_id).one()
    if signer.signed_at is not None or not _active(request):
        raise NotFound()
    now = datetime.now(UTC)
    signer.signed_at = now
    signer.signed_name = typed_name
    signer.signed_ip = ip
    signer.signed_user_agent = user_agent
    db.flush()
    event_bus.publish(
        db,
        "document.signature.signed",
        {
            "startup_id": str(request.startup_id),
            "request_id": str(request.id),
            "signer_id": str(signer.id),
        },
    )
    signers = _signers(db, request)
    if all(s.signed_at is not None for s in signers):
        request.status = SignatureRequestStatus.complete
        request.completed_at = now
        db.flush()
        event_bus.publish(
            db,
            "document.signature.completed",
            {"startup_id": str(request.startup_id), "request_id": str(request.id)},
        )
    return request


def request_status(request: SignatureRequest) -> str:
    if request.status == SignatureRequestStatus.awaiting and _is_expired(request):
        return "expired"
    return request.status.value


def serialize_request(
    db: Session, request: SignatureRequest, *, with_signers: bool = True
) -> dict[str, Any]:
    file = db.query(DocumentFile).filter_by(id=request.file_id).first()
    signers = _signers(db, request)
    out: dict[str, Any] = {
        "id": str(request.id),
        "title": request.title,
        "status": request_status(request),
        "file_id": str(request.file_id),
        "filename": file.filename if file else None,
        "signed_count": sum(1 for s in signers if s.signed_at is not None),
        "total": len(signers),
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "completed_at": request.completed_at.isoformat() if request.completed_at else None,
        "created_at": request.created_at.isoformat(),
    }
    if with_signers:
        out["signers"] = [
            {
                "email": s.email,
                "name": s.name,
                "position": s.position,
                "status": "signed" if s.signed_at is not None else "pending",
                "signed_at": s.signed_at.isoformat() if s.signed_at else None,
                "signed_name": s.signed_name,
            }
            for s in signers
        ]
    return out
