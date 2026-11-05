"""Serve-time authorisation and delivery for the investor viewer.

`authorise` runs on EVERY request that touches an item (open, content, heartbeat), against fresh
rows, so a revoked or expired share, a removed item, a folder no longer in scope, a trashed
Documents file or a file that is no longer scan-clean stops working at once (D-FUND-L4). Anything
that fails is the same 404.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.data_room import DataRoomItem, DataRoomShare, DataRoomView
from app.db.models.document import Document, DocumentFile
from app.db.models.funding import Investor
from app.platform.events import event_bus
from app.platform.storage import get_storage
from app.services.documents.export import render_pdf
from app.services.documents.template_defs import disclaimer_for
from app.services.funding import data_room as room
from app.services.funding.data_room_shares import status_of
from app.services.funding.watermark import image_to_pdf, stamp_pdf, unstampable

# One heartbeat can add at most this much, so a stalled tab or a replayed call can't inflate time.
MAX_HEARTBEAT_GAP = 45


class Authorised:
    def __init__(
        self,
        item: DataRoomItem,
        doc: Document | None,
        file: DocumentFile | None,
    ) -> None:
        self.item, self.doc, self.file = item, doc, file

    @property
    def name(self) -> str:
        return self.file.filename if self.file is not None else (self.doc.title or "Untitled")  # type: ignore[union-attr]


def authorise(db: Session, share: DataRoomShare, item_id: uuid.UUID) -> Authorised:
    """The item, only if this share may show it right now. Raises the uniform 404 otherwise."""
    db.refresh(share)  # never trust an in-memory copy: revocation must be seen
    if status_of(share) != "active":
        raise NotFound()
    item = db.query(DataRoomItem).filter_by(id=item_id, startup_id=share.startup_id).first()
    if item is None or str(item.folder_id) not in set(share.scopes):
        raise NotFound()
    doc, file = room.live_target(db, item)
    if doc is None and file is None:
        raise NotFound()
    return Authorised(item, doc, file)


def visible_tree(db: Session, share: DataRoomShare) -> dict[str, Any]:
    """What the investor may browse: only the scoped folders, only items viewable right now."""
    db.refresh(share)
    if status_of(share) != "active":
        raise NotFound()
    scope = set(share.scopes)
    folders = [f for f in room.list_folders(db, share.startup_id) if str(f.id) in scope]
    out = []
    for f in folders:
        items = (
            db.query(DataRoomItem)
            .filter_by(folder_id=f.id, startup_id=share.startup_id)
            .order_by(DataRoomItem.position, DataRoomItem.created_at, DataRoomItem.id)
            .all()
        )
        shown = []
        for it in items:
            doc, file = room.live_target(db, it)
            if doc is None and file is None:
                continue
            facts = room.facts(doc, file)
            shown.append(
                {
                    "id": str(it.id),
                    "name": facts["name"],
                    "content_type": facts["content_type"],
                    "size_bytes": facts["size_bytes"],
                    "viewer": facts["viewer"],
                    "viewable": room.viewable(
                        facts["content_type"], allow_download=share.allow_download
                    ),
                }
            )
        out.append({"id": str(f.id), "name": f.name, "items": shown})
    return {"folders": out, "allow_download": share.allow_download}


def _investor_name(db: Session, share: DataRoomShare) -> str | None:
    if share.investor_id is None:
        return None
    name: str | None = db.query(Investor.name).filter(Investor.id == share.investor_id).scalar()
    return name


def open_item(
    db: Session, share: DataRoomShare, auth: Authorised, *, viewer_email: str, iph: str | None
) -> DataRoomView:
    """Log a view (every open is logged), touch the share, and announce the first view of the day
    (D-FUND-L6: one notification per share per day, not per document)."""
    now = datetime.now(UTC)
    view = DataRoomView(
        startup_id=share.startup_id,
        share_id=share.id,
        item_id=auth.item.id,
        item_name=auth.name[:255],
        viewer_email=viewer_email,
        started_at=now,
        last_seen_at=now,
        duration_s=0,
        ip_hash=iph,
    )
    db.add(view)
    db.flush()
    # Atomic "first view today": exactly one concurrent open sees the row come back.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first_today = (
        db.execute(
            update(DataRoomShare)
            .where(
                DataRoomShare.id == share.id,
                or_(
                    DataRoomShare.last_viewed_at.is_(None),
                    DataRoomShare.last_viewed_at < day_start,
                ),
            )
            .values(last_viewed_at=now)
            .returning(DataRoomShare.id)
        ).first()
        is not None
    )
    if not first_today:
        db.execute(
            update(DataRoomShare)
            .where(DataRoomShare.id == share.id, DataRoomShare.last_viewed_at < now)
            .values(last_viewed_at=now)
        )
    if first_today:
        event_bus.publish(
            db,
            "funding.dataroom.viewed",
            {
                "startup_id": str(share.startup_id),
                "share_id": str(share.id),
                "investor_id": str(share.investor_id) if share.investor_id else None,
                "investor_name": _investor_name(db, share),
                "viewer_email": viewer_email,
                "document": view.item_name,
                "href": "/funding/data-room",
            },
        )
    return view


def get_view(db: Session, share: DataRoomShare, view_id: uuid.UUID) -> DataRoomView | None:
    return db.query(DataRoomView).filter_by(id=view_id, share_id=share.id).first()


def heartbeat(db: Session, share: DataRoomShare, view: DataRoomView) -> int:
    """Add the time since the last beat (capped) so `duration_s` is real. The caller has already
    re-authorised the item, so a revoked share or removed document stops counting (404)."""
    now = datetime.now(UTC)
    gap = (now - view.last_seen_at).total_seconds()
    view.duration_s += int(max(0.0, min(gap, MAX_HEARTBEAT_GAP)))
    view.last_seen_at = now
    db.flush()
    return view.duration_s


def assert_viewable(share: DataRoomShare, auth: Authorised) -> None:
    """With downloads off, only what we can watermark may be shown. Anything else would leave as
    raw bytes, so it is refused (422) until the founder allows downloads or uploads a PDF. Checked
    before a view is logged."""
    if auth.file is not None and not room.viewable(
        auth.file.content_type, allow_download=share.allow_download
    ):
        raise AppError(
            "VIEW_ONLY_UNSUPPORTED",
            "This file type can't be shown view-only. Ask the sender for a PDF.",
            422,
        )


def deliver(
    db: Session, share: DataRoomShare, auth: Authorised, *, viewer_email: str
) -> tuple[bytes, str, str]:
    """(bytes, content_type, filename) ready to send. A PDF is stamped with the viewer's email
    and the time; a rich document is rendered to PDF then stamped. Other types are served as they
    are (view-only unless the share allows download, D-FUND-8)."""
    if auth.file is not None:
        ctype = auth.file.content_type
        assert_viewable(share, auth)
        raw = get_storage().read_private(auth.file.storage_key)
        if raw is None:
            raise NotFound()
        if ctype == room.PDF:
            return stamp_pdf(raw, email=viewer_email), room.PDF, auth.file.filename
        if ctype in room.IMAGE_TYPES and not share.allow_download:
            pdf = image_to_pdf(raw)
            return stamp_pdf(pdf, email=viewer_email), room.PDF, f"{auth.file.filename}.pdf"
        return raw, ctype, auth.file.filename
    doc = auth.doc
    if doc is None:
        raise unstampable()
    pdf = render_pdf(doc, disclaimer_for(doc.template_key, doc.kind))
    return stamp_pdf(pdf, email=viewer_email), room.PDF, f"{doc.title or 'document'}.pdf"
