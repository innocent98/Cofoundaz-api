import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext, require_super_admin
from app.core.envelope import Meta, success_response
from app.db.models.enums import ApprovalStatus
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.staff import (
    ApprovalCreateRequest,
    Reason,
    StaffActionRequest,
    StaffInviteRequest,
    StaffRevokeRequest,
    StaffUpdateRequest,
)
from app.services.staff import access, audit_explorer, danger_zone
from app.services.staff.stepup import verify_step_up

router = APIRouter()

_Ctx = Annotated[StaffContext, Depends(require_super_admin)]
_Db = Annotated[Session, Depends(get_db)]


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.get("/staff")
def list_staff(ctx: _Ctx, db: _Db, include_revoked: bool = True) -> dict[str, Any]:
    return success_response(access.list_staff(db, include_revoked=include_revoked))


@router.post("/staff", status_code=201)
def invite_staff(
    payload: StaffInviteRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = access.invite_staff(
        db,
        ctx,
        email=payload.email,
        role=payload.role,
        scopes=payload.scopes,
        reason=payload.reason,
        ip=ip,
        user_agent=ua,
    )
    return success_response(row)


@router.patch("/staff/{staff_id}")
def update_staff(
    staff_id: uuid.UUID, payload: StaffUpdateRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = access.update_staff(
        db,
        ctx,
        staff_id=staff_id,
        role=payload.role,
        scopes=payload.scopes,
        reason=payload.reason,
        ip=ip,
        user_agent=ua,
    )
    return success_response(row)


@router.post("/staff/{staff_id}/revoke")
def revoke_staff(
    staff_id: uuid.UUID, payload: StaffRevokeRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = access.revoke_staff(
        db, ctx, staff_id=staff_id, reason=payload.reason, ip=ip, user_agent=ua
    )
    return success_response(row)


@router.get("/danger-zone")
def danger_zone_overview(ctx: _Ctx, db: _Db) -> dict[str, Any]:
    return success_response(danger_zone.overview(db, ctx))


@router.get("/approvals")
def list_approvals(
    ctx: _Ctx,
    db: _Db,
    status: ApprovalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    rows, next_cursor = danger_zone.list_requests(db, status=status, limit=limit, cursor=cursor)
    return success_response({"requests": rows}, Meta(next_cursor=next_cursor))


@router.post("/approvals", status_code=201)
def request_approval(
    payload: ApprovalCreateRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = danger_zone.request_action(
        db,
        ctx,
        action=payload.action,
        reason=payload.reason,
        message=payload.message,
        approver_user_id=payload.approver_user_id,
        ip=ip,
        user_agent=ua,
    )
    return success_response(row)


@router.post("/approvals/{request_id}/approve")
def approve_request(
    request_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = danger_zone.decide(
        db, ctx, request_id, approve=True, reason=payload.reason, ip=ip, user_agent=ua
    )
    return success_response(row)


@router.post("/approvals/{request_id}/reject")
def reject_request(
    request_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = danger_zone.decide(
        db, ctx, request_id, approve=False, reason=payload.reason, ip=ip, user_agent=ua
    )
    return success_response(row)


@router.post("/approvals/{request_id}/cancel")
def cancel_request(
    request_id: uuid.UUID, payload: StaffActionRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    row = danger_zone.cancel(db, ctx, request_id, reason=payload.reason, ip=ip, user_agent=ua)
    return success_response(row)


@router.post("/maintenance/disable")
def disable_maintenance(
    payload: StaffActionRequest, request: Request, ctx: _Ctx, db: _Db
) -> dict[str, Any]:
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    new = danger_zone.disable_maintenance(db, ctx, reason=payload.reason, ip=ip, user_agent=ua)
    return success_response({"maintenance": new})


@router.get("/audit")
def audit_explorer_list(
    ctx: _Ctx,
    db: _Db,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=120)] = None,
    workspace_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    rows, next_cursor = audit_explorer.explore(
        db,
        actor_id=actor_id,
        action=action,
        startup_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor,
    )
    return success_response({"entries": rows}, Meta(next_cursor=next_cursor))


@router.get("/audit/export")
def audit_explorer_export(
    request: Request,
    ctx: _Ctx,
    db: _Db,
    reason: Reason,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=120)] = None,
    workspace_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Response:
    body, count = audit_explorer.export_csv(
        db,
        actor_id=actor_id,
        action=action,
        startup_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
    )
    ip, ua = _client(request)
    write_audit(
        db,
        "audit.exported",
        actor_user_id=ctx.user.id,
        entity_type="audit_log",
        reason=reason,
        details={
            "rows": count,
            "filters": {
                "actor_id": str(actor_id) if actor_id else None,
                "action": action,
                "workspace_id": str(workspace_id) if workspace_id else None,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
            },
        },
        ip=ip,
        user_agent=ua,
    )
    db.commit()
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
