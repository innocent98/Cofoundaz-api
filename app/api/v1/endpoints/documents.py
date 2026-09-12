import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.document import Document
from app.db.models.enums import DocumentKind, DocumentStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.email import EmailMessage, get_email_sender
from app.schemas.document import DocumentCreate, DocumentSave, ShareCreate
from app.services.documents.files import (
    EXT_BY_CONTENT_TYPE,
    delete_file,
    get_file,
    list_files,
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
from app.services.documents.template_defs import catalog, instantiate, template_view

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)
_MAX_FILE_BYTES = 15 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


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
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"templates": catalog()})


@router.get("/document-templates/{key}")
def get_template(
    key: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response(template_view(key))


@router.get("/documents")
def list_documents_endpoint(
    kind: str | None = None,
    folder: str | None = None,
    status: str | None = None,  # noqa: A002 - query name is part of the FE contract
    membership: Membership = Depends(require_workspace),  # noqa: B008
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
    if file.content_type not in EXT_BY_CONTENT_TYPE:
        raise AppError(
            "VALIDATION_ERROR",
            "That file type isn't allowed. Upload a PDF, Office doc, image, text, or CSV.",
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
    row = upload_file(
        db,
        _startup(db, membership),
        uploaded_by_id=membership.user_id,
        filename=file.filename or "file",
        content_type=file.content_type,
        size_bytes=len(content),
        content=content,
        folder=folder,
    )
    db.commit()
    return success_response(serialize_file(row))


@router.get("/documents/files")
def list_document_files(
    folder: str | None = None,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_files(db, _startup(db, membership), folder=folder)
    return success_response({"files": [serialize_file(r) for r in rows]})


@router.get("/documents/files/{file_id}")
def get_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_file(get_file(db, membership, file_id)))


@router.delete("/documents/files/{file_id}")
def delete_document_file(
    file_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    delete_file(db, get_file(db, membership, file_id))
    db.commit()
    return success_response({"deleted": True})


@router.get("/documents/shares")
def list_workspace_shares_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
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
    link = f"{settings.SERVER_HOST}/shared/{raw}"
    get_email_sender().send(
        EmailMessage(
            to=body.email,
            subject="A document was shared with you on Cofoundaz",
            html=f'<p>You can view the document here: <a href="{link}">{link}</a></p>',
        )
    )
    db.commit()
    return success_response({**serialize_share(share), "link": link})


@router.get("/documents/{document_id}/shares")
def list_shares_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
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
def open_shared_endpoint(
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    share = open_shared(db, token)  # 404 unknown/expired/revoked
    doc = db.query(Document).filter_by(id=share.document_id).one()
    db.commit()  # persists last_viewed_at
    return success_response(
        {
            "document": serialize_document(doc),
            "access_level": share.access_level.value,
            "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        }
    )


@router.get("/documents/{document_id}")
def get_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
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
