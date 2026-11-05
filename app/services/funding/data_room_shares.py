"""Data room shares (founder side): create, list, change, revoke, and the access log.

The link token is `HMAC(SECRET_KEY, "dataroom:{share_id}:{version}")` (256 bits, same pattern as
Documents shares): only its SHA-256 sits in the database, an editor can re-copy the link, and
rotating SECRET_KEY kills every outstanding link. See `services/documents/links.py`.
"""

import html
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.core.logger import log
from app.db.models.data_room import DataRoomFolder, DataRoomShare, DataRoomView
from app.db.models.funding import Investor
from app.db.models.startup import Startup
from app.platform.email import EmailMessage, get_email_sender
from app.schemas.data_room import ShareCreate, ShareUpdate
from app.services.documents.links import derive_token, token_hash
from app.services.documents.shares import resolve_expiry
from app.services.funding import data_room as room
from app.services.funding.errors import _validation

PURPOSE = "dataroom"
MAX_ACTIVE_SHARES = 200


def fe_base_url() -> str:
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")


def raw_token(share: DataRoomShare) -> str:
    return derive_token(PURPOSE, share.id, share.token_version)


def link_for(share: DataRoomShare) -> str:
    return f"{fe_base_url()}/dr/{raw_token(share)}"


def status_of(share: DataRoomShare, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if share.revoked_at is not None:
        return "revoked"
    if share.expires_at is not None and share.expires_at <= now:
        return "expired"
    return "active"


def _folders_in_workspace(
    db: Session, startup_id: uuid.UUID, folder_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(folder_ids))
    found = {
        r[0]
        for r in db.query(DataRoomFolder.id)
        .filter(DataRoomFolder.startup_id == startup_id, DataRoomFolder.id.in_(wanted))
        .all()
    }
    if found != set(wanted):
        raise _validation("folder_ids", "One of those folders isn't in your data room.")
    return wanted


def get_share(db: Session, startup_id: uuid.UUID, share_id: uuid.UUID) -> DataRoomShare:
    row = db.query(DataRoomShare).filter_by(id=share_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def _invite_email(share: DataRoomShare, company: str, link: str) -> None:
    """Best-effort: the share exists whether or not the email goes out; the link is also returned
    to the founder."""
    safe = html.escape(company)
    try:
        get_email_sender().send(
            EmailMessage(
                to=share.email,
                subject=f"{company} shared their data room with you".replace("\n", " ")[:200],
                html=(
                    f"<p><strong>{safe}</strong> invited you to their data room on Cofoundaz.</p>"
                    f'<p><a href="{html.escape(link, quote=True)}">Open the data room</a></p>'
                    "<p>You will be asked for a 6-digit code sent to this address. The link only "
                    "works for this email.</p>"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - deliberately tolerant; the share still exists
        log.warning(f"data room invite email failed: {exc!r}")


def create_share(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, data: ShareCreate
) -> tuple[DataRoomShare, str]:
    scopes = _folders_in_workspace(db, startup_id, data.folder_ids)
    round_id = None
    if data.investor_id is not None:
        inv = db.query(Investor).filter_by(id=data.investor_id, startup_id=startup_id).first()
        if inv is None:
            raise _validation("investor_id", "That investor isn't in your pipeline.")
        round_id = inv.round_id
    active = (
        db.query(func.count(DataRoomShare.id))
        .filter(DataRoomShare.startup_id == startup_id, DataRoomShare.revoked_at.is_(None))
        .scalar()
    )
    if active >= MAX_ACTIVE_SHARES:
        raise AppError("TOO_MANY_SHARES", "Revoke some links before creating more.", 409)
    expires_at = resolve_expiry(expires_at=data.expires_at, expires_in_days=data.expires_in_days)
    share_id = uuid.uuid4()
    raw = derive_token(PURPOSE, share_id, 0)
    row = DataRoomShare(
        id=share_id,
        startup_id=startup_id,
        round_id=round_id,
        investor_id=data.investor_id,
        created_by_id=actor_id,
        email=str(data.email).strip().lower(),
        scopes=[str(f) for f in scopes],
        allow_download=data.allow_download,
        expires_at=expires_at,
        token_hash=token_hash(raw),
        token_version=0,
    )
    db.add(row)
    db.flush()
    if data.send_email:
        company = db.query(Startup.name).filter(Startup.id == startup_id).scalar() or "A founder"
        _invite_email(row, company, f"{fe_base_url()}/dr/{raw}")
    return row, raw


def update_share(
    db: Session, startup_id: uuid.UUID, share_id: uuid.UUID, data: ShareUpdate
) -> DataRoomShare:
    row = get_share(db, startup_id, share_id)
    if row.revoked_at is not None:
        raise NotFound()  # a revoked link stays revoked; create a new one
    given = data.model_fields_set
    if data.folder_ids is not None:
        row.scopes = [str(f) for f in _folders_in_workspace(db, startup_id, data.folder_ids)]
    if data.allow_download is not None:
        row.allow_download = data.allow_download
    if "expires_at" in given or "expires_in_days" in given:
        row.expires_at = resolve_expiry(
            expires_at=data.expires_at, expires_in_days=data.expires_in_days
        )
    db.flush()
    return row


def revoke_share(db: Session, startup_id: uuid.UUID, share_id: uuid.UUID) -> DataRoomShare:
    """Immediate: every serve and heartbeat re-reads this row."""
    row = get_share(db, startup_id, share_id)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.flush()
    return row


def _stats(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not ids:
        return {}
    rows = (
        db.query(
            DataRoomView.share_id, func.count(DataRoomView.id), func.sum(DataRoomView.duration_s)
        )
        .filter(DataRoomView.share_id.in_(ids))
        .group_by(DataRoomView.share_id)
        .all()
    )
    return {r[0]: (int(r[1]), int(r[2] or 0)) for r in rows}


def serialize_shares(db: Session, rows: list[DataRoomShare]) -> list[dict[str, Any]]:
    stats = _stats(db, [r.id for r in rows])
    names = {
        r.id: r.name
        for r in db.query(Investor.id, Investor.name).filter(
            Investor.id.in_([s.investor_id for s in rows if s.investor_id])
        )
    }
    out = []
    for s in rows:
        status = status_of(s)
        views, seconds = stats.get(s.id, (0, 0))
        out.append(
            {
                "id": str(s.id),
                "email": s.email,
                "investor_id": str(s.investor_id) if s.investor_id else None,
                "investor_name": names.get(s.investor_id) if s.investor_id else None,
                "folder_ids": list(s.scopes),
                "allow_download": s.allow_download,
                "watermark": True,
                "status": status,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "revoked_at": s.revoked_at.isoformat() if s.revoked_at else None,
                "last_viewed_at": s.last_viewed_at.isoformat() if s.last_viewed_at else None,
                "view_count": views,
                "total_duration_s": seconds,
                "unviewable_items": (
                    room.unviewable_items(db, s.startup_id, list(s.scopes))
                    if not s.allow_download
                    else []
                ),
                "link": link_for(s) if status == "active" else None,
                "created_at": s.created_at.isoformat(),
            }
        )
    return out


def list_shares(
    db: Session, startup_id: uuid.UUID, *, investor_id: uuid.UUID | None = None
) -> list[DataRoomShare]:
    q = db.query(DataRoomShare).filter_by(startup_id=startup_id)
    if investor_id is not None:
        q = q.filter_by(investor_id=investor_id)
    return q.order_by(DataRoomShare.created_at.desc(), DataRoomShare.id).all()


def access_log(
    db: Session,
    startup_id: uuid.UUID,
    *,
    share_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    q = db.query(DataRoomView).filter_by(startup_id=startup_id)
    if share_id is not None:
        q = q.filter_by(share_id=share_id)
    total = q.count()
    rows = (
        q.order_by(DataRoomView.started_at.desc(), DataRoomView.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    share_rows = {
        s.id: s
        for s in db.query(DataRoomShare).filter(DataRoomShare.id.in_({r.share_id for r in rows}))
    }
    inv_ids = {s.investor_id for s in share_rows.values() if s.investor_id}
    names = {
        i.id: i.name for i in db.query(Investor.id, Investor.name).filter(Investor.id.in_(inv_ids))
    }
    return {
        "total": total,
        "items": [
            {
                "id": str(v.id),
                "share_id": str(v.share_id),
                "viewer_email": v.viewer_email,
                "investor_name": names.get(share_rows[v.share_id].investor_id),
                "document": v.item_name,
                "item_id": str(v.item_id) if v.item_id else None,
                "started_at": v.started_at.isoformat(),
                "duration_s": v.duration_s,
            }
            for v in rows
        ],
    }
