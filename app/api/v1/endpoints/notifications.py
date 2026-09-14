import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
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
