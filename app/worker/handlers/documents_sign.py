"""Build the executed PDF after the last signature and email it to every party.

Job `documents.signature_finalize` (payload `{request_id}`), enqueued by `record_signature` in the
transaction that completes the request. Idempotent and resumable, because it commits as it goes
(`manages_transactions=True`):

1. Stamp: original pages + signature page + audit certificate -> a private, scanned DocumentFile,
   linked as `executed_file_id`. Skipped on retry once linked.
2. Email: one message per party (each signer, then the sender), each stamped when sent, so a
   retry after a mail failure only resends to those not yet emailed.
"""

import html
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.document import DocumentFile, SignatureRequest, SignatureSigner
from app.db.models.enums import ScanStatus, SignatureRequestStatus
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.email import EmailAttachment, EmailMessage, get_email_sender
from app.platform.events import event_bus
from app.platform.storage import get_storage
from app.services.documents.common import display_name
from app.services.documents.esign_pdf import (
    LEGAL_CAVEAT,
    Certificate,
    SignerStamp,
    build_executed_pdf,
)
from app.services.documents.folders import ensure_path
from app.services.documents.links import sha256_hex
from app.services.documents.scanner import scan_file
from app.services.documents.signatures import FINALIZE_JOB, describe_link_access
from app.worker.runner import register_handler


def _safe(text: str) -> str:
    """One line, no CR/LF: safe in an email subject or filename."""
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _executed_filename(source: DocumentFile) -> str:
    stem = source.filename.rsplit(".", 1)[0] if "." in source.filename else source.filename
    return f"{_safe(stem)[:240]} - signed.pdf"


def build_executed_file(db: Session, req: SignatureRequest) -> DocumentFile:
    source = db.get(DocumentFile, req.file_id)
    if source is None:
        raise RuntimeError("the signed file no longer exists")
    content = get_storage().read_private(source.storage_key)
    if content is None:
        raise RuntimeError("the signed file's content is missing")
    signers = (
        db.query(SignatureSigner)
        .filter_by(request_id=req.id)
        .order_by(SignatureSigner.position)
        .all()
    )
    startup = db.get(Startup, req.startup_id)
    cert = Certificate(
        request_id=str(req.id),
        title=req.title,
        filename=source.filename,
        sender=display_name(db, req.created_by_id)
        or (startup.name if startup and startup.name else "Cofoundaz"),
        sent_at=req.created_at,
        completed_at=req.completed_at or req.created_at,
        document_hash=req.document_hash or sha256_hex(content),
        signers=[
            SignerStamp(
                name=s.name or s.email,
                email=s.email,
                signed_name=s.signed_name or "",
                signed_at=s.signed_at,  # type: ignore[arg-type]  # every signer has signed
                ip=s.signed_ip,
                user_agent=s.signed_user_agent,
                viewed_at=s.viewed_at,
                link_copies=[describe_link_access(e) for e in s.link_copies],
            )
            for s in signers
        ],
    )
    pdf = build_executed_pdf(content, cert)
    key = f"documents/{req.startup_id}/{uuid.uuid4().hex}.pdf"
    get_storage().save_private(key, pdf, "application/pdf")
    row = DocumentFile(
        startup_id=req.startup_id,
        uploaded_by_id=req.created_by_id,
        folder=source.folder,
        filename=_executed_filename(source),
        content_type="application/pdf",
        size_bytes=len(pdf),
        storage_key=key,
        url=None,
        is_private=True,
        scan_status=ScanStatus.pending,
    )
    db.add(row)
    db.flush()
    ensure_path(db, req.startup_id, source.folder)
    scan_file(db, row, pdf)
    req.executed_file_id = row.id
    req.executed_hash = sha256_hex(pdf)
    db.flush()
    event_bus.publish(
        db,
        "document.signature.executed",
        {"startup_id": str(req.startup_id), "request_id": str(req.id), "file_id": str(row.id)},
    )
    return row


def render_executed_email(req: SignatureRequest, signers: list[SignatureSigner]) -> str:
    rows = "".join(
        f"<li>{html.escape(s.signed_name or s.email)} ({html.escape(s.email)})</li>"
        for s in signers
    )
    return (
        "<div style='font-family:system-ui,sans-serif;max-width:560px'>"
        f"<h2>{html.escape(req.title)} is fully signed</h2>"
        "<p>Everyone has signed. The executed PDF, with its signature page and audit "
        "certificate, is attached. Keep it for your records.</p>"
        f"<ul>{rows}</ul>"
        f"<p style='font-size:12px;color:#666'>{html.escape(LEGAL_CAVEAT)}</p></div>"
    )


def _send(
    to: str, req: SignatureRequest, signers: list[SignatureSigner], file: DocumentFile
) -> None:
    pdf = get_storage().read_private(file.storage_key)
    if pdf is None:
        raise RuntimeError("the executed file's content is missing")
    get_email_sender().send(
        EmailMessage(
            to=to,
            subject=_safe(f"Signed: {req.title}")[:200],
            html=render_executed_email(req, signers),
            attachments=[
                EmailAttachment(filename=file.filename, content=pdf, content_type="application/pdf")
            ],
        )
    )


def _claim(db: Session, model: Any, row_id: uuid.UUID, column: str) -> bool:
    """Atomically stamp `column` (still NULL) before sending, and commit. Only the job whose
    UPDATE matched may send, so two jobs for one request never email the same party twice (the
    row lock the handler took is released by its earlier commit)."""
    col = getattr(model, column)
    res = db.execute(
        update(model).where(model.id == row_id, col.is_(None)).values({column: datetime.now(UTC)})
    )
    db.commit()
    return bool(res.rowcount == 1)  # type: ignore[attr-defined]


def _unclaim(db: Session, model: Any, row_id: uuid.UUID, column: str) -> None:
    """The send failed: free the claim so the retry (or another job) sends it."""
    db.execute(update(model).where(model.id == row_id).values({column: None}))
    db.commit()


def handle_signature_finalize(db: Session, job: Job) -> None:
    req = (
        db.query(SignatureRequest)
        .filter_by(id=uuid.UUID(str(job.payload["request_id"])))
        .with_for_update()
        .first()
    )
    if req is None or req.status != SignatureRequestStatus.complete:
        return  # gone, or (impossible today) not complete: nothing to execute
    if req.executed_file_id is None:
        build_executed_file(db, req)
        db.commit()  # the file is durable before any email goes out
    file = db.get(DocumentFile, req.executed_file_id)
    if file is None:
        raise RuntimeError("the executed file row is missing")
    signers = (
        db.query(SignatureSigner)
        .filter_by(request_id=req.id)
        .order_by(SignatureSigner.position)
        .all()
    )
    failures: list[str] = []
    for s in signers:
        if not _claim(db, SignatureSigner, s.id, "executed_emailed_at"):
            continue  # already sent, or another job is sending it right now
        try:
            _send(s.email, req, signers, file)
        except Exception as exc:  # noqa: BLE001 - keep going; the retry resends only the failed
            failures.append(f"{type(exc).__name__}")
            log.warning(f"executed copy to signer {s.id} failed: {exc!r}")
            _unclaim(db, SignatureSigner, s.id, "executed_emailed_at")
    owner = db.get(User, req.created_by_id) if req.created_by_id else None
    if (
        owner is not None
        and owner.email
        and _claim(db, SignatureRequest, req.id, "owner_emailed_at")
    ):
        try:
            _send(owner.email, req, signers, file)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{type(exc).__name__}")
            log.warning(f"executed copy to sender of {req.id} failed: {exc!r}")
            _unclaim(db, SignatureRequest, req.id, "owner_emailed_at")
    if failures:
        raise RuntimeError(f"{len(failures)} executed-copy email(s) failed; will retry")


register_handler(FINALIZE_JOB, handle_signature_finalize, manages_transactions=True)
