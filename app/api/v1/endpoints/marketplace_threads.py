"""Founder-side conversations (`/marketplace/threads*`) and uploads (`/marketplace/files*`), slice D.
Founders and team members read; only a founder starts a conversation, posts or uploads (D-MP-L1).
Staff do not read threads (D-MP-L11: only inside a dispute, slice E)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, FileNotClean
from app.core.rate_limit import limiter
from app.db.models.enums import (
    MARKETPLACE_ROLES,
    MARKETPLACE_WRITE_ROLES,
    MarketplaceFilePurpose,
    ScanStatus,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.marketplace import MessageCreate, ThreadOpen
from app.services.marketplace import files, outbox, threads

router = APIRouter()

_Reader = Annotated[Membership, Depends(require_role(*MARKETPLACE_ROLES))]
_Founder = Annotated[Membership, Depends(require_role(*MARKETPLACE_WRITE_ROLES))]
_User = Annotated[User, Depends(get_verified_user)]
_Db = Annotated[Session, Depends(get_db)]
_CHUNK = 1024 * 1024


@router.get("/threads")
def list_threads(
    m: _Reader,
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    items, total, unread = threads.list_founder(db, m, limit=limit, offset=offset)
    return success_response(
        {
            "threads": items,
            "unread_total": unread,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(items) < total,
            },
        }
    )


@router.post("/threads")
@limiter.limit("20/minute")
def open_thread(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    payload: ThreadOpen,
    response: Response,
    m: _Founder,
    user: _User,
    db: _Db,
) -> dict[str, Any]:
    """The Message button: get the conversation with an expert, creating it the first time."""
    thread, created = threads.open_thread(db, user, m, payload.provider_id)
    db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(threads.thread_view(db, thread, threads.FOUNDER))


@router.get("/threads/{thread_id}")
def get_thread(thread_id: uuid.UUID, m: _Reader, db: _Db) -> dict[str, Any]:
    return success_response(threads.get_founder(db, m, thread_id))


@router.get("/threads/{thread_id}/messages")
def list_messages(
    thread_id: uuid.UUID,
    m: _Reader,
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The newest messages first page back with `before` (a message id); each page is oldest to
    newest. Opening a thread marks the expert's messages read for the whole workspace."""
    items, more = threads.read_founder(db, m, thread_id, limit=limit, before=before)
    db.commit()
    return success_response({"messages": items, "has_more": more})


@router.post("/threads/{thread_id}/messages", status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def post_message(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    thread_id: uuid.UUID,
    payload: MessageCreate,
    m: _Founder,
    user: _User,
    db: _Db,
) -> dict[str, Any]:
    msg = threads.post_founder(db, user, m, thread_id, body=payload.body, file_ids=payload.file_ids)
    db.commit()
    outbox.flush(db)
    return success_response(threads.serialize_message(db, msg, threads.FOUNDER))


# ------------------------------------------------------------------ files


@router.post("/files", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def upload_file(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    m: _Founder,
    user: _User,
    db: _Db,
    purpose: Annotated[MarketplaceFilePurpose, Form()],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Upload a brief or message attachment (PDF, Word, Excel, PNG, JPEG; 10 MB). Scanned and
    stored privately; it is shared only once you attach it to a booking or send it in a message."""
    if purpose not in files.FOUNDER_UPLOADS:
        raise AppError("VALIDATION_ERROR", "Choose 'brief' or 'message' as the purpose.", 422)
    limit = files.size_limit(purpose)
    content = b""
    while chunk := await file.read(_CHUNK):
        content += chunk
        if len(content) > limit:
            raise AppError(
                "FILE_TOO_LARGE", f"File must be {limit // (1024 * 1024)} MB or smaller.", 422
            )
    row = files.store_file(
        db,
        owner_user_id=user.id,
        purpose=purpose,
        filename=file.filename,
        content_type=file.content_type,
        content=content,
    )
    db.commit()
    return success_response(files.serialize_file(row))


@router.get("/files/{file_id}", response_class=Response)
@router.get("/files/{file_id}/content", response_class=Response)
def download_file(file_id: uuid.UUID, m: _Reader, user: _User, db: _Db) -> Response:
    """Download. Your own upload, or a file the expert sent you in one of your bookings and
    conversations (founder and team member); anyone else, another workspace's file included, 404."""
    row = files.readable_by_founder_side(db, user.id, m.startup_id, file_id)
    if row.scan_status != ScanStatus.clean:
        raise FileNotClean()
    return Response(
        content=files.read_bytes(row),
        media_type=row.mime,
        headers=files.download_headers(row.filename),
    )


@router.delete("/files/{file_id}")
def delete_file(file_id: uuid.UUID, m: _Founder, user: _User, db: _Db) -> dict[str, Any]:
    """Remove an upload you haven't sent yet. A file already attached to a booking or message
    stays: the other side may have it."""
    row = files.get_owned(db, user.id, file_id)
    if row.booking_id is not None or row.thread_id is not None:
        raise AppError("FILE_IN_USE", "That file was already sent, so it can't be removed.", 409)
    files.erase(db, row)
    db.commit()
    return success_response({"deleted": True})
