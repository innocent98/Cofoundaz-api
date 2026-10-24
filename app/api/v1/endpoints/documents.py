import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.core.logger import log
from app.core.rate_limit import limiter
from app.db.models.document import Document, DocumentFile, SignatureRequest
from app.db.models.enums import DOCUMENT_ROLES, DocumentKind, DocumentStatus, ScanStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.email import EmailMessage, get_email_sender
from app.platform.signed_urls import InvalidSignedToken, verify_key
from app.schemas.document import (
    DocumentCreate,
    DocumentSave,
    ShareCreate,
    SignAction,
    SignatureRequestCreate,
)
from app.services.documents.files import (
    FILE_LINK_TTL_SECONDS,
    delete_file,
    ensure_private,
    file_by_key,
    file_link,
    get_file,
    list_files,
    local_path,
    purge_object,
    serialize_file,
    upload_file,
)
from app.services.documents.service import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    serialize_document,
    serialize_summary,
    update_document,
    validate_sections,
)
from app.services.documents.shares import (
    _share,
    create_share,
    list_shares,
    list_workspace_shares,
    open_shared,
    revoke_share,
    serialize_share,
    share_document,
)
from app.services.documents.signatures import (
    cancel_request,
    create_request,
    get_request,
    list_requests,
    open_for_signing,
    record_signature,
    reissue_unsigned,
    serialize_request,
)
from app.services.documents.sniff import declared_type, sniff_content_type
from app.services.documents.template_defs import catalog, instantiate, template_view

router = APIRouter()
# D-DOC-1: every authenticated Documents route is founder + team member only (reads included)
# until per-person grants land. The two public token routes (/shared/{token}, /sign/{token}) are
# the only ones outside this gate: the token is the credential.
_editor = require_role(*DOCUMENT_ROLES)
_MAX_FILE_BYTES = 15 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


def _fe_base_url() -> str:
    """The FRONTEND app origin for emailed deep links, falling back to the API origin.

    Share (`/shared/{token}`) and signing (`/sign/{token}`) links are meant to open
    FE pages, not API routes -- so they must be built off `APP_BASE_URL` (the FE
    origin), not `SERVER_HOST` (the API origin). Mirrors the auth-email pattern in
    app/services/auth/emails.py::_base_url so both flows resolve links the same way.
    """
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")


def _api_public_url() -> str:
    return settings.API_PUBLIC_URL or settings.SERVER_HOST


def _file_response(file: DocumentFile) -> FileResponse:
    path = local_path(file)
    if path is None:
        raise NotFound()
    safe = file.filename.replace('"', "").replace("\r", "").replace("\n", "")
    return FileResponse(
        path,
        media_type=file.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": f'attachment; filename="{safe}"',
        },
    )


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


def _signature_email(to: str, link: str, title: str) -> None:
    """Best-effort: a flaky mail backend must not fail the request."""
    try:
        get_email_sender().send(
            EmailMessage(
                to=to,
                subject=f"Signature requested: {title}",
                html=f'<p>You have a document to review and sign: <a href="{link}">{link}</a></p>',
            )
        )
    except Exception as exc:  # noqa: BLE001 - delivery is best-effort
        log.warning(f"signature email to {to} failed: {exc}")


def get_request_by_id(db: Session, request_id: uuid.UUID) -> SignatureRequest:
    return db.query(SignatureRequest).filter_by(id=request_id).one()


def _parse_kind(kind: str | None) -> DocumentKind | None:
    if kind is None:
        return None
    try:
        return DocumentKind(kind)
    except ValueError:
        raise NotFound() from None


def _parse_status(value: str | None) -> DocumentStatus | None:
    if value is None:
        return None
    try:
        return DocumentStatus(value)
    except ValueError:
        raise NotFound() from None


@router.get("/document-templates")
def list_templates(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"templates": catalog()})


@router.get("/document-templates/{key}")
def get_template(
    key: str,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response(template_view(key))


@router.get("/documents")
def list_documents_endpoint(
    kind: str | None = None,
    folder: str | None = None,
    status: str | None = None,  # noqa: A002 - query name is part of the FE contract
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_documents(
        db,
        _startup(db, membership),
        kind=_parse_kind(kind),
        folder=folder,
        status=_parse_status(status),
    )
    return success_response({"documents": [serialize_summary(r) for r in rows]})


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def create_document_endpoint(
    body: DocumentCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    if body.template_key:
        kind, title, sections = instantiate(body.template_key)  # 404 if unknown
        title = body.title or title
    else:
        kind = body.kind or DocumentKind.custom
        title = body.title or ""
        sections = validate_sections(body.sections or [])
    doc = create_document(
        db,
        startup,
        created_by_id=membership.user_id,
        kind=kind,
        title=title,
        sections=sections,
        folder=body.folder,
        template_key=body.template_key,
    )
    db.commit()
    return success_response(serialize_document(doc))


@router.post("/documents/files", status_code=status.HTTP_201_CREATED)
async def upload_document_file(
    file: UploadFile = File(...),  # noqa: B008
    folder: str | None = Form(None),  # noqa: B008
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    filename = file.filename or "file"
    declared = declared_type(file.content_type, filename)
    if declared is None:
        raise AppError(
            "VALIDATION_ERROR",
            "That file type isn't allowed. Upload a PDF, Office doc, image, text, Markdown, or CSV.",
            422,
        )
    content = b""
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        content += chunk
        if len(content) > _MAX_FILE_BYTES:
            raise AppError("VALIDATION_ERROR", "File must be 15 MB or smaller.", 422)
    # The header is a claim; the bytes decide. A mismatch (an .exe sent as application/pdf) is
    # refused before anything reaches storage.
    content_type = sniff_content_type(content, declared)
    if content_type is None:
        raise AppError(
            "VALIDATION_ERROR",
            "That file's contents don't match its type, or the file is empty.",
            422,
        )
    row = upload_file(
        db,
        _startup(db, membership),
        uploaded_by_id=membership.user_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        content=content,
        folder=folder,
    )
    db.commit()
    return success_response(serialize_file(row))


@router.get("/documents/files")
def list_document_files(
    folder: str | None = None,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_files(db, _startup(db, membership), folder=folder)
    return success_response({"files": [serialize_file(r) for r in rows]})


@router.get("/documents/files/download", response_class=FileResponse)
@limiter.shared_limit("30/minute", scope="public-doc-file")
def download_document_file(
    request: Request,
    token: str = Query(..., min_length=1, max_length=2000),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FileResponse:
    """Serves a locally stored file to the holder of a valid, unexpired signed link. The link is
    only issued by `GET /documents/files/{id}/download` after the role and scan checks; expired,
    tampered, unknown, or no-longer-clean is the same 404."""
    try:
        claim = verify_key(token)
    except InvalidSignedToken:
        raise NotFound() from None
    file = file_by_key(db, startup_id=claim.startup_id, key=claim.key)
    if file is None or file.scan_status != ScanStatus.clean:
        raise NotFound()
    return _file_response(file)


@router.get("/documents/files/{file_id}")
def get_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_file(get_file(db, membership, file_id)))


@router.get("/documents/files/{file_id}/download")
def download_document_file_link(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The only way to read an uploaded file: a signed link that expires in a few minutes.
    Founder / team member only, and only for a `clean` file."""
    file = get_file(db, membership, file_id)
    if not file.is_private:
        # Only a legacy file needs the move (and so the lock); a normal request never locks.
        db.refresh(file, with_for_update=True)
    link = file_link(file, api_public_url=_api_public_url())
    db.commit()  # persists a legacy file's move behind authentication
    return success_response(link)


@router.delete("/documents/files/{file_id}")
def delete_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    # Row lock first: a concurrent "send for signature" on this file waits, then finds it gone
    # (404) or, if it won the race, makes this delete see the request (409). The stored object
    # is removed only after the commit succeeds.
    target = delete_file(db, get_file(db, membership, file_id, lock=True))
    db.commit()
    purge_object(target)
    return success_response({"deleted": True})


@router.get("/documents/shares")
def list_workspace_shares_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_workspace_shares(db, _startup(db, membership))
    return success_response({"shares": [serialize_share(r, with_document=True) for r in rows]})


@router.post("/documents/{document_id}/shares", status_code=status.HTTP_201_CREATED)
def create_share_endpoint(
    document_id: uuid.UUID,
    body: ShareCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = share_document(db, membership, document_id)
    share, raw = create_share(
        db,
        doc,
        shared_by_id=membership.user_id,
        email=body.email,
        access_level=body.access_level,
        expires_in_days=body.expires_in_days,
    )
    link = f"{_fe_base_url()}/shared/{raw}"
    # Best-effort: the share is created regardless of email delivery. The link is
    # returned in the response so the founder can copy it even if the mail send
    # fails (a flaky provider must not 500 share creation). Auth/invite sends
    # stay fail-loud; only this notification is tolerant.
    try:
        get_email_sender().send(
            EmailMessage(
                to=body.email,
                subject="A document was shared with you on Cofoundaz",
                html=f'<p>You can view the document here: <a href="{link}">{link}</a></p>',
            )
        )
    except Exception as exc:  # noqa: BLE001 - deliberately tolerant; share still created
        log.warning(f"share notification email to {body.email} failed: {exc!r}")
    db.commit()
    return success_response({**serialize_share(share), "link": link})


@router.get("/documents/{document_id}/shares")
def list_shares_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = share_document(db, membership, document_id)
    return success_response({"shares": [serialize_share(r) for r in list_shares(db, doc)]})


@router.delete("/documents/{document_id}/shares/{share_id}")
def revoke_share_endpoint(
    document_id: uuid.UUID,
    share_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    revoke_share(db, _share(db, membership, document_id, share_id))
    db.commit()
    return success_response({"revoked": True})


@router.get("/shared/{token}")
# One bucket per caller across every token (a per-URL bucket would let a caller guess tokens freely).
@limiter.shared_limit("30/minute", scope="public-doc-share")
def open_shared_endpoint(
    request: Request,
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    share = open_shared(db, token)  # 404 unknown/expired/revoked
    doc = db.query(Document).filter_by(id=share.document_id).one()
    db.commit()  # persists last_viewed_at
    return success_response(
        {
            # No internal id or folder: the token holder needs the content, not our bookkeeping.
            "document": {
                k: v for k, v in serialize_document(doc).items() if k not in ("id", "folder")
            },
            "access_level": share.access_level.value,
            "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        }
    )


@router.get("/documents/signature-requests")
def list_signature_requests_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_requests(db, _startup(db, membership))
    return success_response({"requests": [serialize_request(db, r) for r in rows]})


@router.post("/documents/files/{file_id}/signature-requests", status_code=status.HTTP_201_CREATED)
def create_signature_request_endpoint(
    file_id: uuid.UUID,
    body: SignatureRequestCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    file = get_file(db, membership, file_id, lock=True)  # 404 if missing/cross-tenant
    title = body.title or file.filename
    req, pairs = create_request(
        db,
        file,
        created_by_id=membership.user_id,
        title=title,
        signers=[s.model_dump() for s in body.signers],
        expires_in_days=body.expires_in_days,
    )
    links = [f"{_fe_base_url()}/sign/{raw}" for _signer, raw in pairs]
    for (signer, _raw), link in zip(pairs, links, strict=True):
        _signature_email(signer.email, link, title)
    db.commit()
    return success_response({**serialize_request(db, req), "signer_links": links})


@router.get("/documents/signature-requests/{request_id}")
def get_signature_request_endpoint(
    request_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_request(db, get_request(db, membership, request_id)))


@router.post("/documents/signature-requests/{request_id}/remind")
def remind_signature_request_endpoint(
    request_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    req = get_request(db, membership, request_id)
    pairs = reissue_unsigned(db, req)  # rotates unsigned signers' tokens; 409 if not active
    for signer, raw in pairs:
        _signature_email(signer.email, f"{_fe_base_url()}/sign/{raw}", req.title)
    db.commit()
    return success_response({"reminded": len(pairs)})


@router.post("/documents/signature-requests/{request_id}/cancel")
def cancel_signature_request_endpoint(
    request_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    cancel_request(db, get_request(db, membership, request_id))  # 409 if not active
    db.commit()
    return success_response({"cancelled": True})


@router.get("/sign/{token}")
@limiter.shared_limit("30/minute", scope="public-sign-read")
def view_for_signing_endpoint(
    request: Request,
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    signer = open_for_signing(db, token)  # 404 unknown/expired/cancelled/complete/signed
    sign_request = get_request_by_id(db, signer.request_id)
    file = db.query(DocumentFile).filter_by(id=sign_request.file_id).one()
    return success_response(
        {
            "request": {
                "title": sign_request.title,
                "status": serialize_request(db, sign_request, with_signers=False)["status"],
            },
            # Only what the signer needs: no internal id, folder or upload time. The content is
            # fetched from `download_url`, which re-checks that this token is still active.
            "file": {
                "filename": file.filename,
                "content_type": file.content_type,
                "size_bytes": file.size_bytes,
                "download_url": f"{_api_public_url().rstrip('/')}/api/v1/sign/{token}/file",
            },
            "signer": {"email": signer.email, "name": signer.name},
        }
    )


@router.get("/sign/{token}/file")
@limiter.shared_limit("30/minute", scope="public-sign-read")
def sign_file_endpoint(
    request: Request,
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    """The file behind a signing link. Valid only while the token is active (not signed, not
    cancelled, not expired, file still clean): the same uniform 404 as the signing view."""
    signer = open_for_signing(db, token)
    sign_request = get_request_by_id(db, signer.request_id)
    file = db.query(DocumentFile).filter_by(id=sign_request.file_id).one()
    was_private = file.is_private
    storage = ensure_private(file)
    if not was_private:
        db.commit()
    remote = storage.private_url(file.storage_key, FILE_LINK_TTL_SECONDS)
    if remote is not None:
        return RedirectResponse(remote, status_code=307)
    return _file_response(file)


@router.post("/sign/{token}")
@limiter.shared_limit("10/minute", scope="public-sign")
def sign_endpoint(
    request: Request,
    token: str,
    body: SignAction,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if not body.typed_name.strip():
        raise AppError("VALIDATION_ERROR", "Type your name to sign.", 422)
    signer = open_for_signing(db, token)  # 404 if not signable
    updated = record_signature(
        db,
        signer,
        typed_name=body.typed_name.strip(),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    # with_signers=False: the public signer must NOT receive the co-signer roster
    # (emails/names). They keep status + signed_count/total (outside the signers block).
    return success_response(serialize_request(db, updated, with_signers=False))


@router.get("/documents/{document_id}")
def get_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_document(get_document(db, membership, document_id)))


@router.put("/documents/{document_id}")
def update_document_endpoint(
    document_id: uuid.UUID,
    body: DocumentSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    update_document(
        db,
        doc,
        title=body.title,
        sections=body.sections,
        status=body.status,
        folder=body.folder,
        expected_version=body.version,
    )
    db.commit()
    return success_response(serialize_document(doc))


@router.delete("/documents/{document_id}")
def delete_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    delete_document(db, doc)
    db.commit()
    return success_response({"deleted": True})
