import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound, SignatureNotActive
from app.db.models.document import DocumentFile, SignatureRequest, SignatureSigner
from app.db.models.enums import JobStatus, ScanStatus, SignatureRequestStatus
from app.db.models.job import Job
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.documents.common import display_name
from app.services.documents.esign_pdf import LEGAL_CAVEAT, validate_pdf
from app.services.documents.files import assert_clean, ensure_private
from app.services.documents.links import derive_token, matches, plausible, sha256_hex, token_hash

FINALIZE_JOB = "documents.signature_finalize"
PDF = "application/pdf"
EXECUTED_ACCESS_DAYS = 90  # how long a signer's link can still fetch the executed copy
MAX_MESSAGE_CHARS = 2000
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def default_message(sender_name: str | None, title: str) -> str:
    return f"{sender_name or 'A teammate'} has sent you {title} to sign via Cofoundaz."


def clean_message(message: str | None) -> str | None:
    """The sender's note with control characters removed (a newline is kept) and trimmed, or None
    when nothing is left. It only ever goes into an email BODY, HTML-escaped; never a header."""
    if message is None:
        return None
    cleaned = _CONTROL.sub("", message.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise AppError(
            "VALIDATION_ERROR",
            f"The message can be at most {MAX_MESSAGE_CHARS} characters.",
            422,
            field_errors=[{"field": "message", "message": "Too long."}],
        )
    return cleaned or None


def signer_token(signer: SignatureSigner) -> str:
    """The signer's raw link token, recomputed (only its hash is stored)."""
    return derive_token("sign", signer.id, signer.token_version)


def _release(signer: SignatureSigner, now: datetime) -> str:
    raw = signer_token(signer)
    signer.token_hash = token_hash(raw)
    signer.notified_at = now
    return raw


def create_request(
    db: Session,
    file: DocumentFile,
    *,
    created_by_id: uuid.UUID,
    title: str,
    signers: list[dict[str, Any]],
    expires_in_days: int | None,
    message: str | None = None,
    sequential: bool = False,
    sender_name: str | None = None,
) -> tuple[SignatureRequest, list[tuple[SignatureSigner, str | None]]]:
    """Create the request. The returned pairs line up with `signers`; a signer who is not released
    yet (sequential mode, not first) has no link, so its token is None."""
    if not signers:
        raise AppError("VALIDATION_ERROR", "At least one signer is required.", 422)
    assert_clean(file)
    if file.content_type != PDF:
        raise AppError(
            "PDF_REQUIRED",
            "Only PDF files can be sent for signature. Export the document to PDF first.",
            422,
        )
    # A legacy public file is moved behind authentication first (the caller commits it).
    content = ensure_private(file).read_private(file.storage_key)
    if content is None:
        raise AppError("FILE_UNAVAILABLE", "This file's content can't be read right now.", 409)
    validate_pdf(content)
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None
    req = SignatureRequest(
        startup_id=file.startup_id,
        file_id=file.id,
        title=title,
        created_by_id=created_by_id,
        expires_at=expires_at,
        message=clean_message(message) or default_message(sender_name, title),
        sequential=sequential,
        document_hash=sha256_hex(content),
    )
    db.add(req)
    db.flush()
    pairs: list[tuple[SignatureSigner, str | None]] = []
    for position, item in enumerate(signers):
        signer = SignatureSigner(
            id=uuid.uuid4(),
            token_version=0,
            request_id=req.id,
            email=item["email"],
            name=item.get("name"),
            # A placeholder nobody holds until the signer is released.
            token_hash=token_hash(secrets.token_urlsafe(32)),
            position=position,
        )
        db.add(signer)
        raw = _release(signer, now) if (not sequential or position == 0) else None
        pairs.append((signer, raw))
    db.flush()
    event_bus.publish(
        db,
        "document.signature.requested",
        {
            "startup_id": str(req.startup_id),
            "request_id": str(req.id),
            "file_id": str(file.id),
            "created_by": str(created_by_id),
        },
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
    """Rotate the link of every released, still-unsigned signer (the old link stops working) and
    return the new ones. A sequential signer who has not had their turn is left alone. 409 if the
    request is no longer active."""
    if not _active(request):
        raise SignatureNotActive()
    pairs: list[tuple[SignatureSigner, str]] = []
    for signer in unsigned_signers(db, request):
        if signer.notified_at is None:
            continue
        signer.token_version += 1
        pairs.append((signer, _release(signer, datetime.now(UTC))))
    db.flush()
    return pairs


def record_link_access(
    db: Session, signer: SignatureSigner, *, user_id: uuid.UUID, via: str
) -> None:
    """Audit: the signer's link was handed to a workspace member (`via` = "created" in the create
    response, "copied" by the copy-link route). Shown to editors and printed on the certificate,
    so the record shows the link left the signer's inbox."""
    user = db.get(User, user_id)
    name = display_name(db, user_id)
    signer.link_copies = [
        *signer.link_copies,
        {
            "user_id": str(user_id),
            "name": name,
            "email": user.email if user else None,
            "at": datetime.now(UTC).isoformat(),
            "via": via,
        },
    ]
    db.flush()


def describe_link_access(entry: dict[str, Any]) -> str:
    # Name only: the certificate goes to external signers, so no member email on it.
    who = entry.get("name") or "a workspace member"
    when = str(entry.get("at", ""))[:19].replace("T", " ")
    how = "in the create response" if entry.get("via") == "created" else "copied"
    return f"Link {how} by {who} at {when} UTC"


def link_for_signer(db: Session, request: SignatureRequest, signer_id: Any) -> str:
    """The current link of one signer, for an editor to copy again. Changes nothing (the signer's
    emailed link keeps working). 404 for another request's signer, 409 when the request is not
    active, the signer has signed, or it is not their turn yet."""
    signer = db.query(SignatureSigner).filter_by(id=signer_id, request_id=request.id).first()
    if signer is None:
        raise NotFound()
    if not _active(request) or signer.signed_at is not None:
        raise SignatureNotActive()
    if signer.notified_at is None:
        raise AppError(
            "SIGNER_NOT_RELEASED",
            "This signer's turn hasn't come yet: their link is released when the signer before "
            "them signs.",
            409,
        )
    raw = signer_token(signer)
    if not matches(signer.token_hash, raw):
        # A pre-v2 signer: their emailed link is a random token we cannot recompute. Rotating
        # would kill the link already in their inbox, so refuse; a reminder mints a derived one.
        raise AppError(
            "LINK_NOT_RECOVERABLE",
            "This signer's link was created before links could be copied. Send a reminder to "
            "give them a new link you can copy.",
            409,
        )
    return raw


def release_next(db: Session, request: SignatureRequest) -> tuple[SignatureSigner, str] | None:
    """Sequential mode: after a signature, release the next unsigned signer's link. None when the
    request is parallel, no longer awaiting, or everyone has been released."""
    if not request.sequential or request.status != SignatureRequestStatus.awaiting:
        return None
    for signer in _signers(db, request):
        if signer.signed_at is None:
            if signer.notified_at is not None:
                return None  # the current signer is already out; nobody else is released yet
            raw = _release(signer, datetime.now(UTC))
            db.flush()
            return signer, raw
    return None


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


def _find_signer(db: Session, token: str) -> SignatureSigner | None:
    if not plausible(token):
        return None
    signer = db.query(SignatureSigner).filter_by(token_hash=token_hash(token)).first()
    if signer is None or not matches(signer.token_hash, token):
        return None
    return signer


def _my_turn(db: Session, request: SignatureRequest, signer: SignatureSigner) -> bool:
    if signer.notified_at is None:
        return False
    if not request.sequential:
        return True
    return all(
        s.signed_at is not None for s in _signers(db, request) if s.position < signer.position
    )


def open_for_signing(db: Session, token: str) -> SignatureSigner:
    signer = _find_signer(db, token)
    # Uniform 404: unknown / already-signed / not their turn / request not active.
    if signer is None or signer.signed_at is not None:
        raise NotFound()
    request = db.query(SignatureRequest).filter_by(id=signer.request_id).one()
    if not _active(request) or not _my_turn(db, request, signer):
        raise NotFound()
    file = db.query(DocumentFile).filter_by(id=request.file_id).one()
    if file.scan_status != ScanStatus.clean:  # blocked after the request went out
        raise NotFound()
    return signer


def mark_viewed(signer: SignatureSigner) -> None:
    """First open of the link stamps `viewed_at`; later opens leave it."""
    if signer.viewed_at is None:
        signer.viewed_at = datetime.now(UTC)


def open_for_executed(
    db: Session, token: str
) -> tuple[SignatureSigner, SignatureRequest, DocumentFile]:
    """A signer's link after the request completed: valid for the executed copy for
    EXECUTED_ACCESS_DAYS, and for nothing else. Same uniform 404 otherwise."""
    signer = _find_signer(db, token)
    if signer is None or signer.signed_at is None:
        raise NotFound()
    request = db.query(SignatureRequest).filter_by(id=signer.request_id).one()
    if (
        request.status != SignatureRequestStatus.complete
        or request.completed_at is None
        or request.completed_at + timedelta(days=EXECUTED_ACCESS_DAYS) <= datetime.now(UTC)
    ):
        raise NotFound()
    if request.executed_file_id is None:
        raise AppError(
            "EXECUTED_NOT_READY", "The executed copy isn't ready yet. Try again shortly.", 409
        )
    file = db.query(DocumentFile).filter_by(id=request.executed_file_id).first()
    if file is None or file.scan_status != ScanStatus.clean:
        raise NotFound()
    return signer, request, file


def record_signature(
    db: Session,
    signer: SignatureSigner,
    *,
    typed_name: str,
    ip: str | None,
    user_agent: str | None,
) -> SignatureRequest:
    # Serialise signers of one request: lock the request row, then re-read this signer under lock.
    # Without it two parallel last signers each see the other as unsigned and nobody completes
    # the request; a double submit from one signer would overwrite the audit fields.
    request = (
        db.query(SignatureRequest)
        .filter_by(id=signer.request_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    db.refresh(signer, with_for_update=True)
    if signer.signed_at is not None or not _active(request):
        raise NotFound()
    now = datetime.now(UTC)
    signer.signed_at = now
    signer.signed_name = typed_name
    signer.signed_ip = ip
    signer.signed_user_agent = user_agent[:512] if user_agent else None
    if signer.viewed_at is None:
        signer.viewed_at = now  # signing implies the document was opened
    db.flush()
    event_bus.publish(
        db,
        "document.signature.signed",
        {
            "startup_id": str(request.startup_id),
            "request_id": str(request.id),
            "signer_id": str(signer.id),
            "owner_id": str(request.created_by_id) if request.created_by_id else None,
            "title": request.title,
            "signer_name": typed_name,
            "href": "/documents/signatures",
        },
    )
    signers = (
        db.query(SignatureSigner)
        .filter_by(request_id=request.id)
        .populate_existing()
        .order_by(SignatureSigner.position.asc())
        .all()
    )
    if all(s.signed_at is not None for s in signers):
        request.status = SignatureRequestStatus.complete
        request.completed_at = now
        db.flush()
        # The executed PDF is built by the worker, in the same transaction as the completion: a
        # rolled-back signature never leaves a job behind, a committed one always does.
        job_dispatcher.enqueue(
            db, FINALIZE_JOB, {"request_id": str(request.id)}, startup_id=request.startup_id
        )
        event_bus.publish(
            db,
            "document.signature.completed",
            {
                "startup_id": str(request.startup_id),
                "request_id": str(request.id),
                "owner_id": str(request.created_by_id) if request.created_by_id else None,
                "title": request.title,
                "href": "/documents/signatures",
            },
        )
    return request


def request_status(request: SignatureRequest) -> str:
    if request.status == SignatureRequestStatus.awaiting and _is_expired(request):
        return "expired"
    return request.status.value


def signer_status(request: SignatureRequest, signer: SignatureSigner) -> str:
    """`signed`, `viewed` (opened, not signed), `pending` (link sent, not opened) or `waiting`
    (sequential: their turn hasn't come)."""
    if signer.signed_at is not None:
        return "signed"
    if signer.notified_at is None:
        return "waiting"
    return "viewed" if signer.viewed_at is not None else "pending"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def executed_info(db: Session, request: SignatureRequest) -> dict[str, Any] | None:
    """The executed-copy block: None until the request completes, then `pending` until the worker
    has built the PDF, then `ready` with the file's id."""
    if request.status != SignatureRequestStatus.complete:
        return None
    if request.executed_file_id is None and request.document_hash is None:
        # Completed before e-sign v2: no executed copy was ever built and none will be.
        return {"state": "legacy", "file_id": None, "filename": None, "size_bytes": None}
    if request.executed_file_id is None:
        # `failed`: every finalize job is dead (attempts exhausted), nothing will retry it
        # until an operator re-queues it. The FE stops its spinner and says so.
        statuses = {
            st
            for (st,) in db.query(Job.status).filter(
                Job.type == FINALIZE_JOB, Job.payload["request_id"].astext == str(request.id)
            )
        }
        live = statuses & {JobStatus.queued, JobStatus.running}
        state = "failed" if JobStatus.failed in statuses and not live else "pending"
        return {"state": state, "file_id": None, "filename": None, "size_bytes": None}
    file = db.query(DocumentFile).filter_by(id=request.executed_file_id).first()
    return {
        "state": "ready" if file else "pending",
        "file_id": str(request.executed_file_id) if file else None,
        "filename": file.filename if file else None,
        "size_bytes": file.size_bytes if file else None,
        "sha256": request.executed_hash,
    }


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
        "sequential": request.sequential,
        "message": request.message,
        "sent_at": request.created_at.isoformat(),
        "expires_at": _iso(request.expires_at),
        "completed_at": _iso(request.completed_at),
        "created_at": request.created_at.isoformat(),
    }
    if with_signers:
        # Editor-only: the public signer routes use with_signers=False (no co-signer roster).
        out["document_hash"] = request.document_hash
        out["executed"] = executed_info(db, request)
        out["legal_caveat"] = LEGAL_CAVEAT
        out["signers"] = [
            {
                "id": str(s.id),
                "email": s.email,
                "name": s.name,
                "position": s.position,
                "status": signer_status(request, s),
                "notified_at": _iso(s.notified_at),
                "viewed_at": _iso(s.viewed_at),
                "signed_at": _iso(s.signed_at),
                "signed_name": s.signed_name,
                "signed_ip": s.signed_ip,
                "signed_user_agent": s.signed_user_agent,
                "link_copies": list(s.link_copies),
            }
            for s in signers
        ]
    return out
