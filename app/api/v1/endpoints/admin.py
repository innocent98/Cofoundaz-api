import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext, require_staff
from app.core.envelope import Meta, success_response
from app.db.models.enums import StartupStage
from app.db.session import get_db
from app.schemas.staff import StaffActionRequest
from app.services.staff import users, workspaces
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
