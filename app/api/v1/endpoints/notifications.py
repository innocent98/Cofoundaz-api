import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import app.platform.realtime as realtime
from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.models.enums import MembershipStatus
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import SessionLocal, get_db
from app.db.tenancy import require_workspace
from app.platform.realtime import channel_for, consume_stream_ticket, mint_stream_ticket
from app.schemas.notification import PreferencesUpdate
from app.services.notifications.preferences import effective_preferences, set_preferences
from app.services.notifications.service import (
    list_notifications,
    mark_all_read,
    mark_read,
    serialize_notification,
    unread_count,
)

router = APIRouter()


@router.get("/notifications")
def list_notifications_endpoint(
    unread: bool = False,
    limit: int = 20,
    cursor: str | None = None,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, next_cursor = list_notifications(
        db,
        user_id=membership.user_id,
        startup_id=membership.startup_id,
        unread=unread,
        limit=limit,
        cursor=cursor,
    )
    return success_response(
        {"notifications": [serialize_notification(n) for n in rows], "next_cursor": next_cursor}
    )


@router.get("/notifications/unread-count")
def unread_count_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"unread": unread_count(db, user_id=membership.user_id, startup_id=membership.startup_id)}
    )


@router.post("/notifications/{notification_id}/read")
def mark_read_endpoint(
    notification_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    n = mark_read(
        db,
        user_id=membership.user_id,
        startup_id=membership.startup_id,
        notification_id=notification_id,
    )
    db.commit()
    return success_response(serialize_notification(n))


@router.post("/notifications/read-all")
def read_all_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    marked = mark_all_read(db, user_id=membership.user_id, startup_id=membership.startup_id)
    db.commit()
    return success_response({"marked": marked})


@router.get("/notifications/preferences")
def get_preferences_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        effective_preferences(db, user_id=membership.user_id, startup_id=membership.startup_id)
    )


@router.put("/notifications/preferences")
def put_preferences_endpoint(
    body: PreferencesUpdate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    eff = set_preferences(
        db,
        user_id=membership.user_id,
        startup_id=membership.startup_id,
        master_email=body.master_email,
        categories=body.categories,
    )
    db.commit()
    return success_response(eff)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # disable nginx buffering for this response (no nginx config change)
    "Connection": "keep-alive",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _active_membership(user_id: str, startup_id: str) -> bool:
    db = SessionLocal()
    try:
        return (
            db.query(Membership)
            .filter(
                Membership.user_id == uuid.UUID(user_id),
                Membership.startup_id == uuid.UUID(startup_id),
                Membership.status == MembershipStatus.active,
            )
            .first()
            is not None
        )
    finally:
        db.close()


def _unread(user_id: str, startup_id: str) -> int:
    db = SessionLocal()
    try:
        return unread_count(db, user_id=uuid.UUID(user_id), startup_id=uuid.UUID(startup_id))
    finally:
        db.close()


async def _event_stream(request: Request, startup_id: str, user_id: str) -> AsyncIterator[str]:
    n = await run_in_threadpool(_unread, user_id, startup_id)
    yield _sse("unread", {"unread": n})
    async with realtime.subscription(channel_for(startup_id, user_id)) as pubsub:
        while not await request.is_disconnected():
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=settings.SSE_HEARTBEAT_INTERVAL
            )
            if msg is None:
                yield ": heartbeat\n\n"
                continue
            payload = json.loads(msg["data"])
            yield _sse(payload["event"], payload["notification"])


@router.post("/notifications/stream-ticket")
def stream_ticket_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"ticket": mint_stream_ticket(str(membership.user_id), str(membership.startup_id))}
    )


@router.get("/notifications/stream")
async def stream_endpoint(request: Request, ticket: str) -> StreamingResponse:
    pair = consume_stream_ticket(ticket)
    if pair is None:
        raise AppError("INVALID_TICKET", "Invalid or expired stream ticket.", 401)
    user_id, startup_id = pair
    if not await run_in_threadpool(_active_membership, user_id, startup_id):
        raise AppError("FORBIDDEN", "No active membership for this workspace.", 403)
    return StreamingResponse(
        _event_stream(request, startup_id, user_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
