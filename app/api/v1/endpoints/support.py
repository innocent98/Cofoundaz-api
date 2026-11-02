import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import Meta, success_response
from app.db.models.enums import TicketStatus
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.schemas.support import MessageRequest, TicketCreateRequest
from app.services.support import founder

router = APIRouter()

_Member = Annotated[Membership, Depends(require_workspace)]
_User = Annotated[User, Depends(get_verified_user)]
_Db = Annotated[Session, Depends(get_db)]


@router.post("/tickets", status_code=201)
def open_ticket(payload: TicketCreateRequest, m: _Member, user: _User, db: _Db) -> dict[str, Any]:
    """Open a ticket from Settings > Help. Rate-limited per person."""
    return success_response(
        founder.create_ticket(
            db, user, m, category=payload.category, subject=payload.subject, body=payload.body
        )
    )


@router.get("/tickets")
def my_tickets(
    m: _Member,
    user: _User,
    db: _Db,
    status: TicketStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, nxt = founder.list_tickets(db, user, m, status=status, limit=limit, cursor=cursor)
    return success_response({"tickets": items}, Meta(next_cursor=nxt))


@router.get("/tickets/{ticket_id}")
def ticket_thread(ticket_id: uuid.UUID, m: _Member, user: _User, db: _Db) -> dict[str, Any]:
    return success_response(founder.get_ticket(db, user, m, ticket_id))


@router.post("/tickets/{ticket_id}/messages", status_code=201)
def reply_to_ticket(
    ticket_id: uuid.UUID, payload: MessageRequest, m: _Member, user: _User, db: _Db
) -> dict[str, Any]:
    return success_response(founder.reply(db, user, m, ticket_id, body=payload.body))
