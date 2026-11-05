"""Funding Hub slice B, founder side: the data room structure, shares and access log.
Founder + team member only (D-FUND-5), behind the `funding` plan module. The investor-facing
routes are in `funding_data_room_public.py`.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.api.v1.endpoints.funding import _funding
from app.core.envelope import success_response
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.data_room import (
    FolderCreate,
    FolderUpdate,
    ItemCreate,
    ItemUpdate,
    ShareCreate,
    ShareUpdate,
)
from app.services.funding import data_room as room
from app.services.funding import data_room_shares as shares

router = APIRouter()


@router.get("/data-room", response_model=dict[str, Any])
def get_data_room(
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = room.tree(db, membership.startup_id)
    out["shares"] = shares.serialize_shares(db, shares.list_shares(db, membership.startup_id))
    return success_response(out)


@router.post("/data-room/setup", response_model=dict[str, Any])
def setup_data_room(
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    room.setup_standard(db, membership.startup_id)
    db.commit()
    return success_response(room.tree(db, membership.startup_id))


@router.post("/data-room/folders", status_code=201, response_model=dict[str, Any])
def create_folder(
    payload: FolderCreate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = room.create_folder(db, membership.startup_id, payload)
    db.commit()
    return success_response({**room.serialize_folder(row), "items": []})


@router.patch("/data-room/folders/{folder_id}", response_model=dict[str, Any])
def update_folder(
    folder_id: uuid.UUID,
    payload: FolderUpdate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = room.update_folder(db, membership.startup_id, folder_id, payload)
    db.commit()
    return success_response(room.serialize_folder(row))


@router.delete("/data-room/folders/{folder_id}", response_model=dict[str, Any])
def delete_folder(
    folder_id: uuid.UUID,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    room.delete_folder(db, membership.startup_id, folder_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/data-room/folders/{folder_id}/items", status_code=201, response_model=dict[str, Any])
def add_item(
    folder_id: uuid.UUID,
    payload: ItemCreate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = room.add_item(db, membership.startup_id, folder_id, payload, actor_id=user.id)
    db.commit()
    return success_response(room.serialize_item(db, row))


@router.patch("/data-room/items/{item_id}", response_model=dict[str, Any])
def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = room.update_item(db, membership.startup_id, item_id, payload)
    db.commit()
    return success_response(room.serialize_item(db, row))


@router.delete("/data-room/items/{item_id}", response_model=dict[str, Any])
def remove_item(
    item_id: uuid.UUID,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    room.remove_item(db, membership.startup_id, item_id)
    db.commit()
    return success_response({"removed": True})


@router.get("/data-room/shares", response_model=dict[str, Any])
def list_shares(
    investor_id: uuid.UUID | None = None,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = shares.list_shares(db, membership.startup_id, investor_id=investor_id)
    return success_response({"items": shares.serialize_shares(db, rows)})


@router.post("/data-room/shares", status_code=201, response_model=dict[str, Any])
def create_share(
    payload: ShareCreate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row, _raw = shares.create_share(db, membership.startup_id, user.id, payload)
    db.commit()
    return success_response(shares.serialize_shares(db, [row])[0])


@router.patch("/data-room/shares/{share_id}", response_model=dict[str, Any])
def update_share(
    share_id: uuid.UUID,
    payload: ShareUpdate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = shares.update_share(db, membership.startup_id, share_id, payload)
    db.commit()
    return success_response(shares.serialize_shares(db, [row])[0])


@router.delete("/data-room/shares/{share_id}", response_model=dict[str, Any])
def revoke_share(
    share_id: uuid.UUID,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = shares.revoke_share(db, membership.startup_id, share_id)
    db.commit()
    return success_response(shares.serialize_shares(db, [row])[0])


@router.get("/data-room/access-log", response_model=dict[str, Any])
def access_log(
    share_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        shares.access_log(db, membership.startup_id, share_id=share_id, limit=limit, offset=offset)
    )
