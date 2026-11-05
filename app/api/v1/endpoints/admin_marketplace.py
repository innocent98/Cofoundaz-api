"""Marketplace vetting for staff (`/admin/marketplace/*`, design §4). Scope `moderation`.

Every mutation carries a reason (10+ characters) and is audited. The decisions that change who
founders trust (approve, reject, suspend, unsuspend) also carry the staff member's own fresh
authenticator code, like the Reported-issues removals. Marking the checklist and asking for
changes are working steps, so they don't."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext, require_staff
from app.api.v1.endpoints.provider import file_headers
from app.core.envelope import Meta, success_response
from app.db.models.enums import ProviderStatus
from app.db.models.marketplace import MarketplaceProvider
from app.db.session import get_db
from app.schemas.marketplace import (
    InviteRequest,
    ReasonRequest,
    RejectRequest,
    RequestChangesRequest,
    StepUpReasonRequest,
    VettingRequest,
)
from app.services.marketplace import emails, providers, vetting
from app.services.staff.stepup import verify_step_up

router = APIRouter()

_Moderation = Annotated[StaffContext, Depends(require_staff("moderation"))]
_Db = Annotated[Session, Depends(get_db)]


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


def _must_exist(db: Session, provider_id: uuid.UUID) -> None:
    """A 404 must not spend the authenticator code: look first, then verify the step-up."""
    if (
        db.query(MarketplaceProvider.id).filter(MarketplaceProvider.id == provider_id).first()
        is None
    ):
        raise providers.err("PROVIDER_NOT_FOUND", "No such expert.", 404)


@router.get("/providers/counts")
def counts(ctx: _Moderation, db: _Db) -> dict[str, Any]:
    return success_response(vetting.counts(db))


@router.get("/providers")
def list_providers(
    ctx: _Moderation,
    db: _Db,
    status_: Annotated[ProviderStatus | None, Query(alias="status")] = None,
    category: Annotated[str | None, Query(max_length=20)] = None,
    query: Annotated[str | None, Query(max_length=120)] = None,
    flagged: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, nxt = vetting.list_providers(
        db,
        status=status_,
        category=category,
        query=query,
        flagged=flagged,
        limit=limit,
        cursor=cursor,
    )
    return success_response({"providers": items}, Meta(next_cursor=nxt))


@router.post("/providers/invite", status_code=status.HTTP_201_CREATED)
def invite(payload: InviteRequest, request: Request, ctx: _Moderation, db: _Db) -> dict[str, Any]:
    ip, ua = _client(request)
    row, outbox = providers.invite_provider(
        db,
        ctx,
        email=str(payload.email).lower(),
        category=payload.category,
        display_name=payload.display_name,
        ip=ip,
        user_agent=ua,
    )
    db.commit()
    undelivered = emails.deliver(outbox)
    return success_response({"provider": vetting.summary(row), "undelivered": undelivered})


@router.get("/providers/{provider_id}")
def get_provider(provider_id: uuid.UUID, ctx: _Moderation, db: _Db) -> dict[str, Any]:
    return success_response(vetting.detail(db, provider_id))


@router.post("/providers/{provider_id}/resend-invite")
def resend_invite(
    provider_id: uuid.UUID, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    row, outbox = providers.resend_invite(db, ctx, provider_id, ip=ip, user_agent=ua)
    db.commit()
    undelivered = emails.deliver(outbox)
    return success_response({"provider": vetting.summary(row), "undelivered": undelivered})


@router.post("/providers/{provider_id}/revoke-invite")
def revoke_invite(
    provider_id: uuid.UUID, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    providers.revoke_invite(db, ctx, provider_id, ip=ip, user_agent=ua)
    db.commit()
    return success_response({"revoked": True})


@router.put("/providers/{provider_id}/vetting")
def put_vetting(
    provider_id: uuid.UUID, payload: VettingRequest, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    checks: dict[str, dict[str, Any]] = {k: v.model_dump() for k, v in payload.checks.items()}
    out = vetting.set_vetting(db, ctx, provider_id, checks, ip=ip, ua=ua)
    db.commit()
    return success_response(out)


@router.post("/providers/{provider_id}/approve")
def approve(
    provider_id: uuid.UUID,
    payload: StepUpReasonRequest,
    request: Request,
    ctx: _Moderation,
    db: _Db,
) -> dict[str, Any]:
    _must_exist(db, provider_id)
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    _, outbox = vetting.approve(db, ctx, provider_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    emails.deliver(outbox)
    return success_response(vetting.detail(db, provider_id))


@router.post("/providers/{provider_id}/reject")
def reject(
    provider_id: uuid.UUID, payload: RejectRequest, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    _must_exist(db, provider_id)
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    _, outbox = vetting.reject(
        db,
        ctx,
        provider_id,
        template=payload.template,
        message=payload.message,
        reason=payload.reason,
        ip=ip,
        ua=ua,
    )
    db.commit()
    emails.deliver(outbox)
    return success_response(vetting.detail(db, provider_id))


@router.post("/providers/{provider_id}/request-changes")
def request_changes(
    provider_id: uuid.UUID,
    payload: RequestChangesRequest,
    request: Request,
    ctx: _Moderation,
    db: _Db,
) -> dict[str, Any]:
    ip, ua = _client(request)
    _, outbox = vetting.request_changes(db, ctx, provider_id, message=payload.message, ip=ip, ua=ua)
    db.commit()
    emails.deliver(outbox)
    return success_response(vetting.detail(db, provider_id))


@router.post("/providers/{provider_id}/suspend")
def suspend(
    provider_id: uuid.UUID,
    payload: StepUpReasonRequest,
    request: Request,
    ctx: _Moderation,
    db: _Db,
) -> dict[str, Any]:
    _must_exist(db, provider_id)
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    _, outbox = vetting.suspend(db, ctx, provider_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    emails.deliver(outbox)
    return success_response(vetting.detail(db, provider_id))


@router.post("/providers/{provider_id}/unsuspend")
def unsuspend(
    provider_id: uuid.UUID,
    payload: StepUpReasonRequest,
    request: Request,
    ctx: _Moderation,
    db: _Db,
) -> dict[str, Any]:
    _must_exist(db, provider_id)
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    vetting.unsuspend(db, ctx, provider_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    return success_response(vetting.detail(db, provider_id))


@router.get("/providers/{provider_id}/files/{file_id}")
def read_file(
    provider_id: uuid.UUID, file_id: uuid.UUID, request: Request, ctx: _Moderation, db: _Db
) -> Response:
    """Staff read an expert's credential or portfolio file. Always audited."""
    ip, ua = _client(request)
    f, data = vetting.open_file(db, ctx, provider_id, file_id, ip=ip, ua=ua)
    db.commit()
    return Response(content=data, media_type=f.mime, headers=file_headers(f.filename))


@router.post("/providers/{provider_id}/clear-flag")
def clear_flag(
    provider_id: uuid.UUID, payload: ReasonRequest, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    """Dismiss a flagged profile edit (the expert stays approved and live). To act on it instead,
    suspend with the usual step-up."""
    ip, ua = _client(request)
    vetting.clear_profile_flag(db, ctx, provider_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    return success_response(vetting.detail(db, provider_id))
