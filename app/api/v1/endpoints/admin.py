import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext, require_staff
from app.core.envelope import Meta, success_response
from app.db.models.enums import StartupStage, TicketCategory, TicketPriority
from app.db.session import get_db
from app.schemas.staff import StaffActionRequest
from app.schemas.support import (
    AssignRequest,
    CannedRequest,
    CannedUpdateRequest,
    EscalateRequest,
    MessageRequest,
    PriorityRequest,
    ReopenRequest,
    ResolveRequest,
)
from app.services.staff import support, users, workspaces
from app.services.staff.stepup import verify_step_up

router = APIRouter()

_Support = Annotated[StaffContext, Depends(require_staff("support"))]
_Anyone = Annotated[StaffContext, Depends(require_staff())]
_Db = Annotated[Session, Depends(get_db)]

BillingStatus = Literal["free", "trialing", "active", "past_due", "canceled"]


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.get("/me")
def staff_me(ctx: _Anyone) -> dict[str, Any]:
    """Who the console thinks the caller is. 404 for anyone who is not active staff."""
    return success_response(
        {
            "user_id": str(ctx.user.id),
            "email": ctx.user.email,
            "role": ctx.staff.role.value,
            "scopes": list(ctx.staff.scopes),
        }
    )


# ---------------------------------------------------------------- users (scope: support)


@router.get("/users")
def list_users(
    ctx: _Support,
    db: _Db,
    query: Annotated[str | None, Query(max_length=120)] = None,
    billing_status: BillingStatus | None = None,
    activity: Literal["active", "dormant"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, next_cursor = users.search_users(
        db,
        query=query,
        billing_status=billing_status,
        activity=activity,
        limit=limit,
        cursor=cursor,
    )
    return success_response({"users": items}, Meta(next_cursor=next_cursor))


@router.get("/users/{user_id}")
def get_user(user_id: uuid.UUID, ctx: _Support, db: _Db) -> dict[str, Any]:
    return success_response(users.user_profile(db, ctx, user_id))


@router.post("/users/{user_id}/suspend")
def suspend(
    user_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    return success_response(
        users.suspend_user(db, ctx, user_id, reason=payload.reason, ip=ip, ua=ua)
    )


@router.post("/users/{user_id}/unsuspend")
def unsuspend(
    user_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    return success_response(
        users.unsuspend_user(db, ctx, user_id, reason=payload.reason, ip=ip, ua=ua)
    )


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    return success_response(
        users.send_password_reset(db, ctx, user_id, reason=payload.reason, ip=ip, ua=ua)
    )


@router.post("/users/{user_id}/resend-verification")
def resend_verification(
    user_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    return success_response(
        users.resend_verification(db, ctx, user_id, reason=payload.reason, ip=ip, ua=ua)
    )


# ---------------------------------------------------------------- workspaces (read-only)


@router.get("/workspaces")
def list_workspaces(
    ctx: _Support,
    db: _Db,
    query: Annotated[str | None, Query(max_length=120)] = None,
    stage: StartupStage | None = None,
    billing_status: BillingStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, next_cursor = workspaces.search_workspaces(
        db, query=query, stage=stage, billing_status=billing_status, limit=limit, cursor=cursor
    )
    return success_response({"workspaces": items}, Meta(next_cursor=next_cursor))


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: uuid.UUID, ctx: _Support, db: _Db) -> dict[str, Any]:
    return success_response(workspaces.workspace_detail(db, workspace_id))


# ---------------------------------------------------------------- dashboard


@router.get("/dashboard/kpis")
def dashboard_kpis(ctx: _Anyone, db: _Db) -> dict[str, Any]:
    return success_response(workspaces.dashboard_kpis(db))


@router.get("/dashboard/attention")
def dashboard_attention(ctx: _Anyone, db: _Db) -> dict[str, Any]:
    """The Needs attention card (A9). A separate route so the KPI payload keeps its shape."""
    return success_response({"tickets": support.attention(db)})


# ---------------------------------------------------------------- support tickets (scope: support)
# No step-up code: staff already signed in with TOTP, and the actions are reversible. Escalate
# needs a written reason; every action writes an audit row.


@router.get("/support/counts")
def support_counts(ctx: _Support, db: _Db) -> dict[str, Any]:
    return success_response(support.counts(db, ctx))


@router.get("/support/assignees")
def support_assignees(ctx: _Support, db: _Db) -> dict[str, Any]:
    return success_response({"staff": support.assignable_staff(db)})


@router.get("/support/tickets")
def list_tickets(
    ctx: _Support,
    db: _Db,
    tab: Literal["open", "mine", "resolved"] = "open",
    priority: TicketPriority | None = None,
    category: TicketCategory | None = None,
    sla: Literal["breached", "due_soon"] | None = None,
    escalated: bool | None = None,
    unassigned: bool = False,
    query: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, nxt = support.list_tickets(
        db,
        ctx,
        tab=tab,
        priority=priority,
        category=category,
        sla=sla,
        escalated=escalated,
        unassigned=unassigned,
        query=query,
        limit=limit,
        cursor=cursor,
    )
    return success_response({"tickets": items}, Meta(next_cursor=nxt))


@router.get("/support/tickets/{ticket_id}")
def get_ticket(ticket_id: uuid.UUID, ctx: _Support, db: _Db) -> dict[str, Any]:
    return success_response(support.detail(db, ticket_id))


@router.post("/support/tickets/{ticket_id}/assign")
def assign_ticket(
    ticket_id: uuid.UUID, payload: AssignRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(
        support.assign(db, ctx, ticket_id, assignee=payload.assignee_user_id, ip=ip, ua=ua)
    )


@router.post("/support/tickets/{ticket_id}/reply")
def reply_ticket(
    ticket_id: uuid.UUID, payload: MessageRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(support.reply(db, ctx, ticket_id, body=payload.body, ip=ip, ua=ua))


@router.post("/support/tickets/{ticket_id}/notes")
def note_ticket(
    ticket_id: uuid.UUID, payload: MessageRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(support.add_note(db, ctx, ticket_id, body=payload.body, ip=ip, ua=ua))


@router.patch("/support/tickets/{ticket_id}/priority")
def prioritise_ticket(
    ticket_id: uuid.UUID, payload: PriorityRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(
        support.set_priority(db, ctx, ticket_id, priority=payload.priority, ip=ip, ua=ua)
    )


@router.post("/support/tickets/{ticket_id}/escalate")
def escalate_ticket(
    ticket_id: uuid.UUID, payload: EscalateRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(
        support.escalate(db, ctx, ticket_id, reason=payload.reason, ip=ip, ua=ua)
    )


@router.post("/support/tickets/{ticket_id}/resolve")
def resolve_ticket(
    ticket_id: uuid.UUID, payload: ResolveRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(
        support.resolve(db, ctx, ticket_id, message=payload.message, ip=ip, ua=ua)
    )


@router.post("/support/tickets/{ticket_id}/reopen")
def reopen_ticket(
    ticket_id: uuid.UUID, payload: ReopenRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(support.reopen(db, ctx, ticket_id, reason=payload.reason, ip=ip, ua=ua))


@router.get("/support/canned")
def list_canned(ctx: _Support, db: _Db) -> dict[str, Any]:
    return success_response({"canned": support.list_canned(db)})


@router.post("/support/canned", status_code=201)
def create_canned(
    payload: CannedRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(
        support.create_canned(db, ctx, title=payload.title, body=payload.body, ip=ip, ua=ua)
    )


@router.patch("/support/canned/{canned_id}")
def update_canned(
    canned_id: uuid.UUID, payload: CannedUpdateRequest, request: Request, ctx: _Support, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(
        support.update_canned(
            db, ctx, canned_id, title=payload.title, body=payload.body, ip=ip, ua=ua
        )
    )


@router.delete("/support/canned/{canned_id}")
def delete_canned(canned_id: uuid.UUID, request: Request, ctx: _Support, db: _Db) -> dict[str, Any]:
    ip, ua = _client(request)
    return success_response(support.delete_canned(db, ctx, canned_id, ip=ip, ua=ua))
