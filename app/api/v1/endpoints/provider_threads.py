"""The expert's conversations (`/provider/threads*`), slice D. An expert replies to conversations
that exist (a founder wrote, or a booking is paid) and never opens one, and sees only their own
threads. Approved experts only."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext, require_provider
from app.core.envelope import success_response
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.schemas.marketplace import MessageCreate
from app.services.marketplace import outbox, threads

router = APIRouter()

_Approved = Annotated[ProviderContext, Depends(require_provider(approved=True))]
_Db = Annotated[Session, Depends(get_db)]


@router.get("/threads")
def list_threads(
    ctx: _Approved,
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    items, total, unread = threads.list_provider(db, ctx, limit=limit, offset=offset)
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


@router.get("/threads/{thread_id}")
def get_thread(thread_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    return success_response(threads.get_provider(db, ctx, thread_id))


@router.get("/threads/{thread_id}/messages")
def list_messages(
    thread_id: uuid.UUID,
    ctx: _Approved,
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: uuid.UUID | None = None,
) -> dict[str, Any]:
    items, more = threads.read_provider(db, ctx, thread_id, limit=limit, before=before)
    db.commit()
    return success_response({"messages": items, "has_more": more})


@router.post("/threads/{thread_id}/messages", status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def post_message(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    thread_id: uuid.UUID,
    payload: MessageCreate,
    ctx: _Approved,
    db: _Db,
) -> dict[str, Any]:
    msg = threads.post_provider(db, ctx, thread_id, body=payload.body, file_ids=payload.file_ids)
    db.commit()
    outbox.flush(db)
    return success_response(threads.serialize_message(db, msg, threads.PROVIDER))
