import html
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound, NotInTrash
from app.core.logger import log
from app.core.rate_limit import limiter
from app.db.models.comment import Comment
from app.db.models.document import Document, DocumentFile, SignatureRequest
from app.db.models.enums import (
    DOCUMENT_ROLES,
    DocumentKind,
    DocumentStatus,
    ScanStatus,
    ShareAccess,
    SignatureRequestStatus,
)
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.email import EmailMessage, get_email_sender
from app.platform.signed_urls import InvalidSignedToken, verify_key
from app.schemas.document import (
    AiDraftRequest,
    DocumentCommentCreate,
    DocumentCreate,
    DocumentPatch,
    DocumentSave,
    ExportRequest,
    FilePatch,
    FolderCreate,
    FolderRename,
    GrantCreate,
    GrantUpdate,
    ShareCreate,
    ShareExpiry,
    SignAction,
    SignatureRequestCreate,
    TemplateInstantiate,
)
from app.services.documents.common import display_name
from app.services.documents.esign_pdf import LEGAL_CAVEAT
from app.services.documents.export import export_document
from app.services.documents.files import (
    FILE_LINK_TTL_SECONDS,
    VIEWER_BY_CONTENT_TYPE,
    delete_file,
    ensure_private,
    file_by_key,
    file_link,
    get_file,
    list_files,
    local_path,
    purge_object,
    restore_file,
    serialize_files,
    trash_file,
    update_file_metadata,
    upload_file,
)
from app.services.documents.folders import (
    create_folder,
    delete_folder,
    folder_tree,
    get_folder,
    rename_folder,
    standard_structure,
)
from app.services.documents.generator import create_from_template, form_for, queue_redraft
from app.services.documents.grants import (
    create_grant,
    get_grant,
    granted_document,
    list_grants,
    list_workspace_grants,
    revoke_grant,
    serialize_grant,
    serialize_shared_item,
    set_permission,
    shared_with_me,
)
from app.services.documents.service import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    restore_document,
    serialize_document,
    serialize_public,
    serialize_summaries,
    trash_document,
    update_document,
    update_metadata,
    validate_sections,
)
from app.services.documents.shares import (
    _share,
    create_share,
    list_file_shares,
    list_shares,
    list_workspace_shares,
    open_shared,
    resolve_expiry,
    revoke_share,
    serialize_share,
    set_expiry,
    share_document,
)
from app.services.documents.signatures import (
    cancel_request,
    create_request,
    get_request,
    link_for_signer,
    list_requests,
    mark_viewed,
    open_for_executed,
    open_for_signing,
    record_signature,
    reissue_unsigned,
    release_next,
    serialize_request,
)
from app.services.documents.sniff import declared_type, sniff_content_type
from app.services.documents.template_defs import catalog, instantiate, template_view
from app.services.documents.versions import (
    get_version,
    list_versions,
    restore_version,
    serialize_version,
    version_authors,
)
from app.services.team import service as team_svc

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


def _file_response(file: DocumentFile, *, inline: bool = False) -> FileResponse:
    path = local_path(file)
    if path is None:
        raise NotFound()
    safe = file.filename.replace('"', "").replace("\r", "").replace("\n", "")
    ascii_name = safe.encode("ascii", "ignore").decode().strip() or "file"
    disposition = (
        f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(safe)}"
    )
    return FileResponse(
        path,
        media_type=file.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": disposition,
        },
    )


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


def _signature_email(
    to: str, link: str, title: str, message: str | None, sender: str | None
) -> None:
    """Best-effort: a flaky mail backend must not fail the request. The sender's message goes
    only into the HTML body, escaped; the subject is the title with line breaks removed, so
    nothing a user typed can add a header."""
    subject = " ".join(f"Signature requested: {title}".split())[:200]
    text = message or f"{sender or 'A teammate'} has sent you {title} to sign via Cofoundaz."
    body = html.escape(text).replace("\n", "<br>")
    try:
        get_email_sender().send(
            EmailMessage(
                to=to,
                subject=subject,
                html=(
                    f"<p>{body}</p>"
                    f'<p><a href="{html.escape(link, quote=True)}">Review and sign</a></p>'
                    f"<p style='font-size:12px;color:#666'>{html.escape(LEGAL_CAVEAT)}</p>"
                ),
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
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The template plus its form state for THIS workspace: what is auto-filled (and from where),
    what is empty, what an AI draft can fill. Nothing is created."""
    view = template_view(key)
    form = form_for(db, _startup(db, membership), membership.user_id, key)
    return success_response({**view, "form": form})


@router.post("/documents/templates/{key}/instantiate", status_code=status.HTTP_201_CREATED)
@limiter.shared_limit("60/hour", scope="doc-instantiate")
def instantiate_template_endpoint(
    request: Request,
    key: str,
    body: TemplateInstantiate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc, form, ai = create_from_template(
        db,
        _startup(db, membership),
        user_id=membership.user_id,
        template_key=key,
        values=body.values,
        title=body.title,
        folder=body.folder,
        want_ai=body.ai_draft or bool(body.ai_sections),
        ai_sections=body.ai_sections,
    )
    db.commit()
    return success_response({"document": serialize_document(doc, db=db), "form": form, "ai": ai})


@router.post("/documents/{document_id}/ai-draft", status_code=status.HTTP_202_ACCEPTED)
@limiter.shared_limit("20/hour", scope="doc-ai-draft")
def redraft_document_endpoint(
    request: Request,
    document_id: uuid.UUID,
    body: AiDraftRequest,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue an AI draft for the document's still-empty AI-draftable sections (the retry after a
    failed draft). Poll `GET /jobs/{job_id}`."""
    doc = get_document(db, membership, document_id)
    ai = queue_redraft(db, _startup(db, membership), doc, body.sections)
    db.commit()
    return success_response(ai)


@router.post("/documents/{document_id}/export")
@limiter.shared_limit("20/minute", scope="doc-export")
def export_document_endpoint(
    request: Request,
    response: Response,
    document_id: uuid.UUID,
    body: ExportRequest,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Export the document as a stored PDF or DOCX file: 201 for a new file, 200 when this
    version was already exported in this format (the same file comes back)."""
    doc = get_document(db, membership, document_id, lock=True)
    row, created = export_document(db, doc, body.format, actor_id=membership.user_id)
    db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(serialize_files(db, [row])[0])


@router.get("/documents")
def list_documents_endpoint(
    kind: str | None = None,
    folder: str | None = None,
    status: str | None = None,  # noqa: A002 - query name is part of the FE contract
    q: str | None = Query(None, max_length=200),  # noqa: B008
    tag: str | None = Query(None, max_length=60),  # noqa: B008
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
        q=q,
        tag=tag,
    )
    return success_response({"documents": serialize_summaries(db, rows)})


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
        tags=body.tags,
    )
    db.commit()
    return success_response(serialize_document(doc, db=db))


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
    return success_response(serialize_files(db, [row])[0])


@router.get("/documents/files")
def list_document_files(
    folder: str | None = None,
    q: str | None = Query(None, max_length=200),  # noqa: B008
    tag: str | None = Query(None, max_length=60),  # noqa: B008
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_files(db, _startup(db, membership), folder=folder, q=q, tag=tag)
    return success_response({"files": serialize_files(db, rows)})


def _serve_signed_file(db: Session, token: str, *, inline: bool) -> FileResponse:
    try:
        claim = verify_key(token)
    except InvalidSignedToken:
        raise NotFound() from None
    file = file_by_key(db, startup_id=claim.startup_id, key=claim.key)
    if file is None or file.scan_status != ScanStatus.clean:
        raise NotFound()
    return _file_response(file, inline=inline)


@router.get("/documents/files/download", response_class=FileResponse)
@limiter.shared_limit("30/minute", scope="public-doc-file")
def download_document_file(
    request: Request,
    token: str = Query(..., min_length=1, max_length=2000),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FileResponse:
    """Serves a locally stored file to the holder of a valid, unexpired signed link. The link is
    only issued by `GET /documents/files/{id}/download` after the role and scan checks; expired,
    tampered, unknown, trashed, or no-longer-clean is the same 404."""
    return _serve_signed_file(db, token, inline=False)


@router.get("/documents/files/content", response_class=FileResponse)
@limiter.shared_limit("30/minute", scope="public-doc-file")
def view_document_file(
    request: Request,
    token: str = Query(..., min_length=1, max_length=2000),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FileResponse:
    """The same signed link served inline for the in-app viewer (PDF.js, images, text)."""
    return _serve_signed_file(db, token, inline=True)


@router.get("/documents/files/{file_id}")
def get_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_files(db, [get_file(db, membership, file_id)])[0])


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


@router.get("/documents/files/{file_id}/content")
def view_document_file_link(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """A short-lived signed link to open the file INLINE in the viewer, with a `viewer` hint
    (pdf, image, text, markdown, csv, docx, xlsx, pptx). Founder / team member, `clean` only."""
    file = get_file(db, membership, file_id)
    if not file.is_private:
        db.refresh(file, with_for_update=True)
    link = file_link(file, api_public_url=_api_public_url(), inline=True)
    db.commit()
    return success_response(link)


@router.patch("/documents/files/{file_id}")
def patch_document_file(
    file_id: uuid.UUID,
    body: FilePatch,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    file = get_file(db, membership, file_id)
    update_file_metadata(db, file, fields={k: getattr(body, k) for k in body.model_fields_set})
    db.commit()
    return success_response(serialize_files(db, [file])[0])


@router.delete("/documents/files/{file_id}")
def delete_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Moves the file to the trash (30 days, then it is purged). A file with a signature request
    is refused (409). Row lock first so a concurrent "send for signature" serialises with it."""
    file = trash_file(db, get_file(db, membership, file_id, lock=True), actor_id=membership.user_id)
    db.commit()
    return success_response(
        {"deleted": True, "trashed_at": file.trashed_at.isoformat() if file.trashed_at else None}
    )


@router.post("/documents/files/{file_id}/restore")
def restore_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    file = restore_file(
        db, get_file(db, membership, file_id, trashed=True, lock=True), actor_id=membership.user_id
    )
    db.commit()
    return success_response(serialize_files(db, [file])[0])


@router.delete("/documents/files/{file_id}/permanent")
def delete_document_file_permanently(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Empties one file from the trash right now. Only a trashed file (409 `NOT_IN_TRASH`
    otherwise). The stored object is removed only after the commit succeeds."""
    file = get_file(db, membership, file_id, trashed=None, lock=True)
    if file.trashed_at is None:
        raise NotInTrash()
    target = delete_file(db, file)
    db.commit()
    purge_object(target)
    return success_response({"deleted": True})


def _share_email(to: str, link: str, title: str) -> None:
    """Best-effort: the share exists whether or not the email goes out (the link is also returned
    to the sharer)."""
    try:
        get_email_sender().send(
            EmailMessage(
                to=to,
                subject="A document was shared with you on Cofoundaz",
                html=(
                    f"<p>You can view <strong>{html.escape(title)}</strong> here: "
                    f'<a href="{html.escape(link, quote=True)}">{html.escape(link)}</a></p>'
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - deliberately tolerant; share still created
        log.warning(f"share notification email to {to} failed: {exc!r}")


def _create_link_share(
    db: Session, membership: Membership, target: Document | DocumentFile, body: ShareCreate
) -> dict[str, Any]:
    expires_at = resolve_expiry(expires_at=body.expires_at, expires_in_days=body.expires_in_days)
    share, raw = create_share(
        db,
        target,
        shared_by_id=membership.user_id,
        email=body.email,
        access_level=body.access_level,
        expires_at=expires_at,
    )
    base = _fe_base_url()
    link = f"{base}/shared/{raw}"
    if body.email:
        _share_email(body.email, link, getattr(target, "title", None) or target.filename)  # type: ignore[union-attr]
    db.commit()
    return success_response(serialize_share(share, link_base=base))


@router.get("/documents/shares")
def list_workspace_shares_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_workspace_shares(db, _startup(db, membership))
    base = _fe_base_url()
    return success_response(
        {"shares": [serialize_share(r, with_document=True, link_base=base) for r in rows]}
    )


@router.post("/documents/{document_id}/shares", status_code=status.HTTP_201_CREATED)
@limiter.shared_limit("60/hour", scope="doc-share-create")
def create_share_endpoint(
    request: Request,
    document_id: uuid.UUID,
    body: ShareCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Share a document: with a person (`email`, who is emailed the link) or with anyone holding
    the link (no `email`). The response carries the link; `GET .../shares` returns it again."""
    doc = share_document(db, membership, document_id)
    return _create_link_share(db, membership, doc, body)


@router.get("/documents/{document_id}/shares")
def list_shares_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = share_document(db, membership, document_id)
    base = _fe_base_url()
    return success_response(
        {"shares": [serialize_share(r, link_base=base) for r in list_shares(db, doc)]}
    )


@router.patch("/documents/{document_id}/shares/{share_id}")
def update_share_endpoint(
    document_id: uuid.UUID,
    share_id: uuid.UUID,
    body: ShareExpiry,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    share = _share(db, membership, document_id, share_id)
    set_expiry(db, share, resolve_expiry(expires_at=body.expires_at, expires_in_days=None))
    db.commit()
    return success_response(serialize_share(share, link_base=_fe_base_url()))


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


@router.post("/documents/files/{file_id}/shares", status_code=status.HTTP_201_CREATED)
@limiter.shared_limit("60/hour", scope="doc-share-create")
def create_file_share_endpoint(
    request: Request,
    file_id: uuid.UUID,
    body: ShareCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Share an uploaded or exported file by link (view only; 409 unless the scan is clean)."""
    file = get_file(db, membership, file_id)
    return _create_link_share(db, membership, file, body)


@router.get("/documents/files/{file_id}/shares")
def list_file_shares_endpoint(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    file = get_file(db, membership, file_id)
    base = _fe_base_url()
    return success_response(
        {"shares": [serialize_share(r, link_base=base) for r in list_file_shares(db, file)]}
    )


@router.patch("/documents/files/{file_id}/shares/{share_id}")
def update_file_share_endpoint(
    file_id: uuid.UUID,
    share_id: uuid.UUID,
    body: ShareExpiry,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    get_file(db, membership, file_id)
    share = _share(db, membership, file_id, share_id)
    set_expiry(db, share, resolve_expiry(expires_at=body.expires_at, expires_in_days=None))
    db.commit()
    return success_response(serialize_share(share, link_base=_fe_base_url()))


@router.delete("/documents/files/{file_id}/shares/{share_id}")
def revoke_file_share_endpoint(
    file_id: uuid.UUID,
    share_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    get_file(db, membership, file_id)
    revoke_share(db, _share(db, membership, file_id, share_id))
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
    expires_at = share.expires_at.isoformat() if share.expires_at else None
    if share.file_id is not None:
        file = db.query(DocumentFile).filter_by(id=share.file_id).one()
        db.commit()
        return success_response(
            {
                "kind": "file",
                "file": {
                    "filename": file.filename,
                    "content_type": file.content_type,
                    "size_bytes": file.size_bytes,
                    "viewer": VIEWER_BY_CONTENT_TYPE.get(file.content_type, "download"),
                    "download_url": f"{_api_public_url().rstrip('/')}/api/v1/shared/{token}/file",
                },
                "access_level": share.access_level.value,
                "expires_at": expires_at,
            }
        )
    doc = db.query(Document).filter_by(id=share.document_id).one()
    db.commit()  # persists last_viewed_at
    return success_response(
        {
            "kind": "document",
            # No internal id or folder: the token holder needs the content, not our bookkeeping.
            "document": serialize_public(doc),
            "access_level": share.access_level.value,
            "expires_at": expires_at,
        }
    )


@router.get("/shared/{token}/file")
@limiter.shared_limit("30/minute", scope="public-doc-share")
def shared_file_endpoint(
    request: Request,
    token: str,
    inline: bool = Query(False),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    """The file behind a file share link, served while the share is live (not revoked, not
    expired, file not trashed and still clean): the same uniform 404 otherwise."""
    share = open_shared(db, token)
    if share.file_id is None:
        raise NotFound()
    file = db.query(DocumentFile).filter_by(id=share.file_id).one()
    storage = ensure_private(file)
    db.commit()  # last_viewed_at, and a legacy file's move behind authentication
    remote = storage.private_url(file.storage_key, FILE_LINK_TTL_SECONDS)
    if remote is not None:
        return RedirectResponse(remote, status_code=307)
    return _file_response(file, inline=inline)


@router.get("/documents/signature-requests")
def list_signature_requests_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_requests(db, _startup(db, membership))
    return success_response({"requests": [serialize_request(db, r) for r in rows]})


@router.post("/documents/files/{file_id}/signature-requests", status_code=status.HTTP_201_CREATED)
@limiter.shared_limit("30/hour", scope="doc-sign-create")
def create_signature_request_endpoint(
    request: Request,
    file_id: uuid.UUID,
    body: SignatureRequestCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Send a PDF for signature. `sequential` releases each signer's link only when the previous
    one has signed; `signer_links` lines up with `signers` and holds null for a signer whose turn
    hasn't come. Only PDFs can be signed (export a document to PDF first)."""
    file = get_file(db, membership, file_id, lock=True)  # 404 if missing/cross-tenant
    title = body.title or file.filename
    sender = display_name(db, membership.user_id)
    req, pairs = create_request(
        db,
        file,
        created_by_id=membership.user_id,
        title=title,
        signers=[s.model_dump() for s in body.signers],
        expires_in_days=body.expires_in_days,
        message=body.message,
        sequential=body.sequential,
        sender_name=sender,
    )
    base = _fe_base_url()
    links: list[str | None] = []
    for signer, raw in pairs:
        link = f"{base}/sign/{raw}" if raw else None
        links.append(link)
        if link:
            _signature_email(signer.email, link, title, req.message, sender)
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


@router.get("/documents/signature-requests/{request_id}/signers/{signer_id}/link")
@limiter.shared_limit("60/hour", scope="doc-sign-link")
def signer_link_endpoint(
    request: Request,
    request_id: uuid.UUID,
    signer_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Copy a signer's link again (editors only). Nothing changes: the emailed link keeps working.
    409 when the request is no longer active, the signer has signed, or (sequential) it isn't
    their turn."""
    req = get_request(db, membership, request_id)
    raw = link_for_signer(db, req, signer_id)
    return success_response({"signer_id": str(signer_id), "link": f"{_fe_base_url()}/sign/{raw}"})


@router.get("/documents/signature-requests/{request_id}/executed")
def executed_signature_request_endpoint(
    request_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """A short-lived signed link to the executed PDF of a completed request (409
    EXECUTED_NOT_READY until the worker has built it)."""
    req = get_request(db, membership, request_id)
    file = (
        db.query(DocumentFile).filter_by(id=req.executed_file_id).first()
        if req.executed_file_id
        else None
    )
    if file is None:
        raise AppError(
            "EXECUTED_NOT_READY", "The executed copy isn't ready yet. Try again shortly.", 409
        )
    return success_response(file_link(file, api_public_url=_api_public_url()))


@router.post("/documents/signature-requests/{request_id}/remind")
@limiter.shared_limit("30/hour", scope="doc-sign-remind")
def remind_signature_request_endpoint(
    request: Request,
    request_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    req = get_request(db, membership, request_id)
    pairs = reissue_unsigned(
        db, req
    )  # rotates released, unsigned signers' links; 409 if not active
    sender = display_name(db, req.created_by_id)
    for signer, raw in pairs:
        _signature_email(
            signer.email, f"{_fe_base_url()}/sign/{raw}", req.title, req.message, sender
        )
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
    signer = open_for_signing(db, token)  # 404 unknown/expired/cancelled/complete/signed/not-yet
    mark_viewed(signer)
    sign_request = get_request_by_id(db, signer.request_id)
    file = db.query(DocumentFile).filter_by(id=sign_request.file_id).one()
    sender = display_name(db, sign_request.created_by_id)
    db.commit()
    return success_response(
        {
            "request": {
                "title": sign_request.title,
                "status": serialize_request(db, sign_request, with_signers=False)["status"],
                "message": sign_request.message,
                "sent_by": sender,
                "sequential": sign_request.sequential,
                "expires_at": (
                    sign_request.expires_at.isoformat() if sign_request.expires_at else None
                ),
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
            "legal_caveat": LEGAL_CAVEAT,
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


@router.get("/sign/{token}/executed")
@limiter.shared_limit("30/minute", scope="public-sign-read")
def sign_executed_endpoint(
    request: Request,
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    """The executed PDF, for a signer whose request completed (their own link, for 90 days after
    completion). 409 EXECUTED_NOT_READY while the worker is still building it."""
    _signer, _req, file = open_for_executed(db, token)
    storage = ensure_private(file)
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
    released = release_next(db, updated)  # sequential: the next signer's turn
    if released is not None:
        nxt, raw = released
        _signature_email(
            nxt.email,
            f"{_fe_base_url()}/sign/{raw}",
            updated.title,
            updated.message,
            display_name(db, updated.created_by_id),
        )
    db.commit()
    # with_signers=False: the public signer must NOT receive the co-signer roster
    # (emails/names). They keep status + signed_count/total (outside the signers block).
    out = serialize_request(db, updated, with_signers=False)
    if updated.status == SignatureRequestStatus.complete:
        out["executed_url"] = f"{_api_public_url().rstrip('/')}/api/v1/sign/{token}/executed"
    return success_response(out)


# --- folders ------------------------------------------------------------------------------
# Declared before `/documents/{document_id}` so the literal segments win over the id parameter.


@router.get("/documents/folders")
def list_folders_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(folder_tree(db, membership.startup_id))


@router.post("/documents/folders", status_code=status.HTTP_201_CREATED)
def create_folder_endpoint(
    body: FolderCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    create_folder(db, membership.startup_id, name=body.name, parent_id=body.parent_id)
    db.commit()
    return success_response(folder_tree(db, membership.startup_id))


@router.post("/documents/folders/standard", status_code=status.HTTP_201_CREATED)
def standard_folders_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    standard_structure(db, membership.startup_id)
    db.commit()
    return success_response(folder_tree(db, membership.startup_id))


@router.patch("/documents/folders/{folder_id}")
def rename_folder_endpoint(
    folder_id: uuid.UUID,
    body: FolderRename,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rename_folder(db, get_folder(db, membership.startup_id, folder_id), name=body.name)
    db.commit()
    return success_response(folder_tree(db, membership.startup_id))


@router.delete("/documents/folders/{folder_id}")
def delete_folder_endpoint(
    folder_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    delete_folder(db, get_folder(db, membership.startup_id, folder_id))
    db.commit()
    return success_response({"deleted": True})


# --- trash --------------------------------------------------------------------------------


@router.get("/documents/trash")
def list_trash_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    docs = list_documents(db, startup, kind=None, folder=None, status=None, trashed=True)
    files = list_files(db, startup, folder=None, trashed=True)
    return success_response(
        {"documents": serialize_summaries(db, docs), "files": serialize_files(db, files)}
    )


# --- per-person grants (D-DOC-1) ----------------------------------------------------------
# Owner side (founder / team member). The grantee side is `/documents/shared-with-me`.


@router.get("/documents/grants")
def list_workspace_grants_endpoint(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_workspace_grants(db, _startup(db, membership))
    return success_response({"grants": [serialize_grant(db, g) for g in rows]})


# --- shared with me (any signed-in, verified user; access comes from a live grant only) ---


@router.get("/documents/shared-with-me")
def shared_with_me_endpoint(
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = shared_with_me(db, user)
    db.commit()  # persists email grants that just bound to this account
    return success_response({"documents": [serialize_shared_item(db, g, d) for g, d in rows]})


@router.get("/documents/shared-with-me/{document_id}")
def get_shared_with_me_endpoint(
    document_id: uuid.UUID,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    grant, doc = granted_document(db, user, document_id)
    db.commit()
    return success_response(serialize_shared_item(db, grant, doc, with_content=True))


@router.get("/documents/shared-with-me/{document_id}/comments")
def list_shared_comments_endpoint(
    document_id: uuid.UUID,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    _grant, doc = granted_document(db, user, document_id)
    return success_response(
        {"comments": [_external_comment(c) for c in _document_comments(db, doc, viewer=user)]}
    )


@router.post("/documents/shared-with-me/{document_id}/comments", status_code=201)
def create_shared_comment_endpoint(
    document_id: uuid.UUID,
    body: DocumentCommentCreate,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    _grant, doc = granted_document(db, user, document_id, need=ShareAccess.comment)
    out = _add_document_comment(db, doc, author=user, body=body.body)
    db.commit()
    return success_response(_external_comment(out))


def _external_comment(c: dict) -> dict:
    """A comment as an outsider sees it: the author's name, but no internal user ids."""
    author = c.get("author")
    return {
        **{k: v for k, v in c.items() if k not in ("mention_user_ids", "author")},
        "author": {"name": author.get("name")} if author else None,
    }


def _document_comments(db: Session, doc: Document, *, viewer: User) -> list[dict]:
    rows = (
        db.query(Comment)
        .filter_by(startup_id=doc.startup_id, entity_type="document", entity_id=doc.id)
        .order_by(Comment.created_at.asc(), Comment.id.asc())
        .all()
    )
    names = team_svc._names(db, {c.author_id for c in rows if c.author_id})
    return [team_svc.serialize_comment(c, names, viewer.id) for c in rows]


def _add_document_comment(db: Session, doc: Document, *, author: User, body: str) -> dict:
    c = Comment(
        startup_id=doc.startup_id,
        entity_type="document",
        entity_id=doc.id,
        author_id=author.id,
        body=body.strip(),
        mention_user_ids=[],
    )
    db.add(c)
    db.flush()
    names = team_svc._names(db, {author.id})
    return team_svc.serialize_comment(c, names, author.id)


# --- one document -------------------------------------------------------------------------


@router.get("/documents/{document_id}")
def get_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_document(get_document(db, membership, document_id), db=db))


@router.put("/documents/{document_id}")
def update_document_endpoint(
    document_id: uuid.UUID,
    body: DocumentSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    # Row lock: two saves from the same version serialise, so the second gets the 409 conflict
    # instead of both claiming the same version number.
    doc = get_document(db, membership, document_id, lock=True)
    update_document(
        db,
        doc,
        title=body.title,
        sections=body.sections,
        status=body.status,
        folder=body.folder,
        expected_version=body.version,
        author_id=membership.user_id,
    )
    db.commit()
    return success_response(serialize_document(doc, db=db))


@router.patch("/documents/{document_id}")
def patch_document_endpoint(
    document_id: uuid.UUID,
    body: DocumentPatch,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Rename, move, tag, or set the status without a new version (the content is untouched)."""
    doc = get_document(db, membership, document_id)
    update_metadata(db, doc, fields={k: getattr(body, k) for k in body.model_fields_set})
    db.commit()
    return success_response(serialize_document(doc, db=db))


@router.delete("/documents/{document_id}")
def delete_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Moves the document to the trash. It vanishes for everyone (library, grantees, token
    links, search) and is purged after 30 days unless restored."""
    doc = trash_document(db, get_document(db, membership, document_id), actor_id=membership.user_id)
    db.commit()
    return success_response(
        {"deleted": True, "trashed_at": doc.trashed_at.isoformat() if doc.trashed_at else None}
    )


@router.post("/documents/{document_id}/restore")
def restore_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = restore_document(
        db,
        get_document(db, membership, document_id, trashed=True),
        actor_id=membership.user_id,
    )
    db.commit()
    return success_response(serialize_document(doc, db=db))


@router.delete("/documents/{document_id}/permanent")
def delete_document_permanently_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Empties one document from the trash right now (409 `NOT_IN_TRASH` if it is not there)."""
    doc = get_document(db, membership, document_id, trashed=None)
    if doc.trashed_at is None:
        raise NotInTrash()
    delete_document(db, doc)
    db.commit()
    return success_response({"deleted": True})


@router.get("/documents/{document_id}/versions")
def list_versions_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    return success_response({"versions": list_versions(db, doc), "current": doc.version})


@router.get("/documents/{document_id}/versions/{number}")
def get_version_endpoint(
    document_id: uuid.UUID,
    number: int,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    row = get_version(db, doc, number)
    names = version_authors(db, [row])
    return success_response(serialize_version(row, names, current=doc.version, with_content=True))


@router.post("/documents/{document_id}/versions/{number}/restore")
def restore_version_endpoint(
    document_id: uuid.UUID,
    number: int,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Makes that version's content the current one as a NEW version (history is kept)."""
    doc = get_document(db, membership, document_id, lock=True)
    restore_version(db, doc, number, author_id=membership.user_id)
    db.commit()
    return success_response(serialize_document(doc, db=db))


@router.get("/documents/{document_id}/grants")
def list_grants_endpoint(
    document_id: uuid.UUID,
    include_revoked: bool = False,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id, trashed=None)
    rows = list_grants(db, doc, include_revoked=include_revoked)
    return success_response({"grants": [serialize_grant(db, g) for g in rows]})


@router.post("/documents/{document_id}/grants", status_code=status.HTTP_201_CREATED)
@limiter.shared_limit("30/hour", scope="doc-grant-create")
def create_grant_endpoint(
    request: Request,
    document_id: uuid.UUID,
    body: GrantCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    grant = create_grant(
        db,
        doc,
        granted_by_id=membership.user_id,
        permission=body.permission,
        email=body.email,
        user_id=body.user_id,
    )
    link = f"{_fe_base_url()}/documents/shared-with-me"
    # Best-effort, like the token-link share: the grant exists whether or not the mail goes out.
    try:
        get_email_sender().send(
            EmailMessage(
                to=grant.grantee_email,
                subject="A document was shared with you on Cofoundaz",
                html=(
                    f"<p>You were given access to a document. Sign in with this email "
                    f'address to open it: <a href="{link}">{link}</a></p>'
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - deliberately tolerant; the grant still exists
        log.warning(f"grant email to {grant.grantee_email} failed: {exc!r}")
    db.commit()
    return success_response(serialize_grant(db, grant))


@router.patch("/documents/{document_id}/grants/{grant_id}")
def update_grant_endpoint(
    document_id: uuid.UUID,
    grant_id: uuid.UUID,
    body: GrantUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id, trashed=None)
    grant = get_grant(db, doc, grant_id)
    if grant.revoked_at is not None:
        raise NotFound()
    set_permission(db, grant, body.permission)
    db.commit()
    return success_response(serialize_grant(db, grant))


@router.delete("/documents/{document_id}/grants/{grant_id}")
def revoke_grant_endpoint(
    document_id: uuid.UUID,
    grant_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id, trashed=None)
    revoke_grant(db, get_grant(db, doc, grant_id), by=membership.user_id)
    db.commit()
    return success_response({"revoked": True})


@router.get("/documents/{document_id}/comments")
def list_document_comments_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    return success_response({"comments": _document_comments(db, doc, viewer=user)})


@router.post("/documents/{document_id}/comments", status_code=201)
def create_document_comment_endpoint(
    document_id: uuid.UUID,
    body: DocumentCommentCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    out = _add_document_comment(db, doc, author=user, body=body.body)
    db.commit()
    return success_response(out)
