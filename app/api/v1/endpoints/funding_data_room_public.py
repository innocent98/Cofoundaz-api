"""Funding Hub slice B, investor side: the public data room viewer (`/funding/dr/{token}`).

No account: the link token plus an emailed 6-digit code (D-FUND-3). Unauthenticated by design, so
every route is rate limited per client address, unknown/revoked/expired links are one uniform 404,
and nothing here trusts an earlier answer: each item request re-reads the share and the Documents
row (`data_room_serve.authorise`). The viewer session travels in `X-Data-Room-Session`, never in
a URL; the file URL uses a separate 2-minute ticket bound to one share and one item.
"""

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.errors import NotFound
from app.core.rate_limit import limiter
from app.db.models.data_room import DataRoomShare
from app.db.models.startup import Startup
from app.db.session import get_db
from app.schemas.data_room import CodeRequest, CodeVerify, Heartbeat
from app.services.funding import data_room_gate as gate
from app.services.funding import data_room_serve as serve

router = APIRouter()

_NO_STORE = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _viewer(share: DataRoomShare, session_token: str | None) -> str:
    return gate.session_email(share, session_token)


@router.get("/dr/{token}", response_model=dict[str, Any])
@limiter.shared_limit("60/minute", scope="data-room-public")
def room_meta(
    request: Request, token: str, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    """Who is sharing, and that a code is needed. Nothing about the invited address."""
    share = gate.resolve_share(db, token)
    company = db.query(Startup.name).filter(Startup.id == share.startup_id).scalar()
    return success_response({"company": company, "requires_code": True})


@router.post("/dr/{token}/otp", status_code=202, response_model=dict[str, Any])
@limiter.shared_limit("10/hour", scope="data-room-otp")
def request_code(
    request: Request,
    token: str,
    payload: CodeRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Always the same answer, whether or not the address is the invited one."""
    share = gate.resolve_share(db, token, lock=True)
    mail = gate.request_code(db, share, str(payload.email), ip=_ip(request))
    db.commit()
    if mail is not None:
        background.add_task(gate.send_code_email, *mail)
    return success_response({"sent": True, "expires_in_s": int(gate.CODE_TTL.total_seconds())})


@router.post("/dr/{token}/verify", response_model=dict[str, Any])
@limiter.shared_limit("20/hour;8/minute", scope="data-room-verify")
def verify_code(
    request: Request,
    token: str,
    payload: CodeVerify,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    share = gate.resolve_share(db, token, lock=True)
    session, expires = gate.verify_code(db, share, str(payload.email), payload.code)
    db.commit()
    return success_response({"session": session, "expires_at": expires.isoformat()})


@router.get("/dr/{token}/tree", response_model=dict[str, Any])
@limiter.shared_limit("60/minute", scope="data-room-public")
def room_tree(
    request: Request,
    token: str,
    x_data_room_session: str | None = Header(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    share = gate.resolve_share(db, token)
    email = _viewer(share, x_data_room_session)
    out = serve.visible_tree(db, share)
    out["viewer_email"] = email
    return success_response(out)


@router.post("/dr/{token}/items/{item_id}/open", response_model=dict[str, Any])
@limiter.shared_limit("120/hour", scope="data-room-open")
def open_item(
    request: Request,
    token: str,
    item_id: uuid.UUID,
    x_data_room_session: str | None = Header(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Start viewing one document: logs the view and returns a 2-minute content URL."""
    share = gate.resolve_share(db, token)
    email = _viewer(share, x_data_room_session)
    auth = serve.authorise(db, share, item_id)
    serve.assert_viewable(share, auth)  # before anything is logged
    view = serve.open_item(db, share, auth, viewer_email=email, iph=gate.ip_hash(_ip(request)))
    ticket = gate.issue_ticket(share, item_id, view.id)
    facts = serve.room.facts(auth.doc, auth.file)
    db.commit()
    return success_response(
        {
            "view_id": str(view.id),
            "name": facts["name"],
            "content_type": facts["content_type"],
            "viewer": facts["viewer"],
            "allow_download": share.allow_download,
            "viewable": True,
            "content_url": f"/api/v1/funding/dr/{token}/items/{item_id}/content?ticket={ticket}",
            "expires_in_s": gate.TICKET_TTL_SECONDS,
            "heartbeat_every_s": 15,
        }
    )


@router.get("/dr/{token}/items/{item_id}/content")
@limiter.shared_limit("120/hour", scope="data-room-content")
def item_content(
    request: Request,
    token: str,
    item_id: uuid.UUID,
    ticket: str | None = Query(default=None, max_length=2000),
    download: bool = False,
    x_data_room_session: str | None = Header(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """The file, authorised again right now. A valid ticket (from `open`) or the session header
    works; with only the session header this call logs the view itself."""
    share = gate.resolve_share(db, token)
    view_id = gate.read_ticket(share, item_id, ticket) if ticket else None
    if view_id is not None:
        email = share.email
    else:
        email = _viewer(share, x_data_room_session)
    auth = serve.authorise(db, share, item_id)
    if view_id is not None and serve.get_view(db, share, view_id) is None:
        raise NotFound()
    serve.assert_viewable(share, auth)
    if view_id is None:
        view = serve.open_item(db, share, auth, viewer_email=email, iph=gate.ip_hash(_ip(request)))
        view_id = view.id
    body, content_type, filename = serve.deliver(db, share, auth, viewer_email=email)
    db.commit()
    attach = download and share.allow_download
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    ascii_name = safe.encode("ascii", "ignore").decode().strip() or "file"
    return Response(
        content=body,
        media_type=content_type,
        headers={
            **_NO_STORE,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": f'{"attachment" if attach else "inline"}; '
            f'filename="{ascii_name}"',
            "X-Data-Room-View": str(view_id),
        },
    )


@router.post("/dr/{token}/heartbeat", response_model=dict[str, Any])
@limiter.shared_limit("30/minute", scope="data-room-heartbeat")
def heartbeat(
    request: Request,
    token: str,
    payload: Heartbeat,
    x_data_room_session: str | None = Header(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    share = gate.resolve_share(db, token)
    _viewer(share, x_data_room_session)
    view = serve.get_view(db, share, payload.view_id)
    if view is None or view.item_id is None:
        raise NotFound()
    serve.authorise(db, share, view.item_id)  # revoked / removed / trashed -> 404, viewer stops
    seconds = serve.heartbeat(db, share, view)
    db.commit()
    return success_response({"duration_s": seconds})
