"""Super Admin danger zone: maintenance mode and global sign-out, each with a second approver.

A Super Admin *requests* maintenance mode or a global sign-out; a different active Super Admin
approves or rejects it; an unanswered request expires. Approving runs the action in the same
transaction that records the approval. Every step is audited. Callers have already passed the
guard and step-up.

Switching maintenance OFF is deliberately not an approval: any one active Super Admin may do it
alone. A state that needs two people to undo can't be undone when only one is available.
"""

import base64
import binascii
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from app.api.staff_deps import StaffContext
from app.core.errors import AppError
from app.db.models.auth import AuthSession
from app.db.models.enums import ApprovalAction, ApprovalStatus, MfaType, StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.staff_approval import StaffApprovalRequest
from app.db.models.user import User, UserProfile
from app.platform import state
from app.platform.audit import write_audit

APPROVAL_TTL = timedelta(hours=1)
MAX_MESSAGE_LENGTH = 280


def _err(code: str, message: str, status: int) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def _already_pending() -> AppError:
    return _err(
        "APPROVAL_ALREADY_PENDING",
        "You already have an open request for this. Cancel it or wait for an answer.",
        409,
    )


def _active_super_admins(
    db: Session, only: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, str, str | None]]:
    """(user id, email, name) of every Super Admin who could pass the staff guard right now."""
    q = (
        db.query(User.id, User.email, UserProfile.full_name)
        .join(PlatformStaff, PlatformStaff.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(
            PlatformStaff.role == StaffRole.super_admin,
            PlatformStaff.revoked_at.is_(None),
            User.deleted_at.is_(None),
            User.status == UserStatus.active,
            User.email_verified_at.is_not(None),
            User.mfa_type == MfaType.totp,
            User.mfa_secret.is_not(None),
        )
        .order_by(User.email)
    )
    if only is not None:
        q = q.filter(User.id == only)
    return [(r[0], r[1], r[2]) for r in q.all()]


def _require_active_actor(db: Session, ctx: StaffContext) -> None:
    """Re-read the caller against the full guard conditions (the guard's copy may be stale)."""
    if not _active_super_admins(db, only=ctx.user.id):
        raise HTTPException(status_code=404, detail="Not Found")


def cancel_open_requests_of(
    db: Session, user_id: uuid.UUID, *, actor: uuid.UUID, reason: str
) -> int:
    """Close every open request a person made or was asked to answer, when they lose Super Admin
    access, so a later reinstatement can't bring an old request back to life."""
    rows = (
        db.query(StaffApprovalRequest)
        .filter(
            StaffApprovalRequest.status == ApprovalStatus.pending,
            (StaffApprovalRequest.requested_by == user_id)
            | (StaffApprovalRequest.designated_approver == user_id),
        )
        .with_for_update()
        .all()
    )
    for req in rows:
        req.status = ApprovalStatus.cancelled
        req.decided_by = actor
        req.decided_at = datetime.now(UTC)
        req.decision_reason = reason
        _audit_request(db, "approval.cancelled", req, actor=actor, reason=reason)
    db.flush()
    return len(rows)


def _person(users: dict[uuid.UUID, tuple[str, str | None]], uid: uuid.UUID | None) -> Any:
    if uid is None:
        return None
    email, name = users.get(uid, (None, None))
    return {"user_id": str(uid), "email": email, "full_name": name}


def _serialize(db: Session, rows: list[StaffApprovalRequest]) -> list[dict[str, Any]]:
    ids = {
        i
        for r in rows
        for i in (r.requested_by, r.designated_approver, r.decided_by)
        if i is not None
    }
    users = (
        {
            u.id: (u.email, p.full_name if p else None)
            for u, p in db.query(User, UserProfile)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .filter(User.id.in_(ids))
            .all()
        }
        if ids
        else {}
    )
    return [
        {
            "id": str(r.id),
            "action": r.action.value,
            "status": r.status.value,
            "reason": r.reason,
            "message": r.payload.get("message"),
            "requested_by": _person(users, r.requested_by),
            "designated_approver": _person(users, r.designated_approver),
            "decided_by": _person(users, r.decided_by),
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "decision_reason": r.decision_reason,
            "outcome": r.outcome,
            "expires_at": r.expires_at.isoformat(),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def _audit_request(
    db: Session,
    action: str,
    req: StaffApprovalRequest,
    *,
    actor: uuid.UUID | None,
    reason: str,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    write_audit(
        db,
        action,
        actor_user_id=actor,
        entity_type="staff_approval_request",
        entity_id=req.id,
        reason=reason,
        details={"action": req.action.value, "requested_by": str(req.requested_by)}
        | (details or {}),
        ip=ip,
        user_agent=user_agent,
    )


def _expire_due(db: Session) -> int:
    """Mark every pending request past its deadline as expired, with an audit row each. Rows another
    transaction is deciding right now are skipped; they're settled by that transaction."""
    due = (
        db.query(StaffApprovalRequest)
        .filter(
            StaffApprovalRequest.status == ApprovalStatus.pending,
            StaffApprovalRequest.expires_at <= datetime.now(UTC),
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for req in due:
        req.status = ApprovalStatus.expired
        _audit_request(
            db,
            "approval.expired",
            req,
            actor=None,
            reason="Nobody approved or rejected it before it expired.",
            details={"expired_at": req.expires_at.isoformat()},
        )
    db.flush()
    return len(due)


def overview(db: Session, ctx: StaffContext) -> dict[str, Any]:
    return {
        "maintenance": state.maintenance_state(db),
        "last_global_signout_at": state.last_global_signout(db),
        "approval_ttl_minutes": int(APPROVAL_TTL.total_seconds() // 60),
        "eligible_approvers": [
            {"user_id": str(uid), "email": email, "full_name": name}
            for uid, email, name in _active_super_admins(db)
            if uid != ctx.user.id
        ],
        "pending_count": db.query(func.count(StaffApprovalRequest.id))
        .filter(
            StaffApprovalRequest.status == ApprovalStatus.pending,
            StaffApprovalRequest.expires_at > datetime.now(UTC),
        )
        .scalar(),
    }


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def list_requests(
    db: Session, *, status: ApprovalStatus | None, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    if _expire_due(db):
        db.commit()
    q = db.query(StaffApprovalRequest)
    if status is not None:
        q = q.filter(StaffApprovalRequest.status == status)
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter(
            (StaffApprovalRequest.created_at < c_ts)
            | ((StaffApprovalRequest.created_at == c_ts) & (StaffApprovalRequest.id < c_id))
        )
    rows = (
        q.order_by(StaffApprovalRequest.created_at.desc(), StaffApprovalRequest.id.desc())
        .limit(limit + 1)
        .all()
    )
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id)
    return _serialize(db, rows), next_cursor


def request_action(
    db: Session,
    ctx: StaffContext,
    *,
    action: ApprovalAction,
    reason: str,
    message: str | None,
    approver_user_id: uuid.UUID | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    if message is not None and action != ApprovalAction.maintenance_enable:
        raise _err("MESSAGE_NOT_ALLOWED", "Only a maintenance request carries a message.", 422)
    others = {uid for uid, _, _ in _active_super_admins(db) if uid != ctx.user.id}
    if approver_user_id is not None and approver_user_id not in others:
        raise _err(
            "APPROVER_INVALID",
            "That person isn't another active Super Admin, so they can't approve this.",
            422,
        )
    if not others:
        raise _err(
            "NO_SECOND_APPROVER",
            "This needs a second active Super Admin to approve it, and there isn't one.",
            422,
        )
    if action == ApprovalAction.maintenance_enable and state.maintenance_state(db)["enabled"]:
        raise _err("MAINTENANCE_ALREADY_ON", "Maintenance mode is already on.", 409)
    _expire_due(db)  # frees this person's slot if their last request has run out
    if (
        db.query(StaffApprovalRequest.id)
        .filter(
            StaffApprovalRequest.action == action,
            StaffApprovalRequest.requested_by == ctx.user.id,
            StaffApprovalRequest.status == ApprovalStatus.pending,
        )
        .first()
    ):
        raise _already_pending()
    now = datetime.now(UTC)
    req = StaffApprovalRequest(
        action=action,
        status=ApprovalStatus.pending,
        requested_by=ctx.user.id,
        reason=reason,
        payload={"message": message} if message else {},
        designated_approver=approver_user_id,
        expires_at=now + APPROVAL_TTL,
    )
    db.add(req)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if req in db:
            db.expunge(req)
        raise _already_pending() from exc  # lost a race with a parallel request of the same kind
    _audit_request(
        db,
        "approval.requested",
        req,
        actor=ctx.user.id,
        reason=reason,
        details={
            "expires_at": req.expires_at.isoformat(),
            "designated_approver": str(approver_user_id) if approver_user_id else None,
            "message": message,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return _serialize(db, [req])[0]


def _load_pending(db: Session, request_id: uuid.UUID) -> StaffApprovalRequest:
    req = (
        db.query(StaffApprovalRequest)
        .filter(StaffApprovalRequest.id == request_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if req is None:
        raise _err("APPROVAL_NOT_FOUND", "No such request.", 404)
    if req.status != ApprovalStatus.pending:
        raise _err("APPROVAL_NOT_PENDING", f"That request is already {req.status.value}.", 409)
    if req.expires_at <= datetime.now(UTC):
        req.status = ApprovalStatus.expired
        _audit_request(
            db,
            "approval.expired",
            req,
            actor=None,
            reason="Nobody approved or rejected it before it expired.",
            details={"expired_at": req.expires_at.isoformat()},
        )
        db.commit()
        raise _err("APPROVAL_EXPIRED", "That request expired before it was answered.", 409)
    return req


def _locked_staff(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, PlatformStaff]:
    rows = (
        db.query(PlatformStaff)
        .filter(PlatformStaff.user_id.in_(user_ids), PlatformStaff.revoked_at.is_(None))
        .order_by(PlatformStaff.id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    return {r.user_id: r for r in rows}


def _run_action(
    db: Session, req: StaffApprovalRequest, ctx: StaffContext, ip: str | None, ua: str | None
) -> dict[str, Any]:
    """Carry out an approved request. Returns the outcome stored on it."""
    if req.action == ApprovalAction.maintenance_enable:
        new = state.set_maintenance(
            db, enabled=True, message=req.payload.get("message"), actor_id=ctx.user.id
        )
        if new is None:
            raise _err("MAINTENANCE_ALREADY_ON", "Maintenance mode is already on.", 409)
        write_audit(
            db,
            "maintenance.enabled",
            actor_user_id=ctx.user.id,
            entity_type="platform_setting",
            reason=req.reason,
            details={"request_id": str(req.id), "message": new["message"]},
            ip=ip,
            user_agent=ua,
        )
        return {"maintenance": new}
    # global_signout: wait out sign-ins in flight, stamp the cutoff, then revoke every refresh token.
    state.lock_session_issue_exclusive(db)
    live = (
        db.query(func.count(AuthSession.id))
        .filter(
            AuthSession.revoked_at.is_(None),
            AuthSession.rotated_at.is_(None),
            AuthSession.expires_at >= datetime.now(UTC),
        )
        .scalar()
    )
    cutoff = state.set_session_cutoff(db, actor_id=ctx.user.id)
    db.query(AuthSession).filter(
        AuthSession.revoked_at.is_(None), AuthSession.expires_at >= cutoff
    ).update({AuthSession.revoked_at: cutoff}, synchronize_session=False)
    write_audit(
        db,
        "platform.global_signout",
        actor_user_id=ctx.user.id,
        entity_type="platform_setting",
        reason=req.reason,
        details={"request_id": str(req.id), "sessions_signed_out": live},
        ip=ip,
        user_agent=ua,
    )
    return {"sessions_signed_out": live, "signed_out_at": cutoff.isoformat()}


def decide(
    db: Session,
    ctx: StaffContext,
    request_id: uuid.UUID,
    *,
    approve: bool,
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    _require_active_actor(db, ctx)
    req = _load_pending(db, request_id)
    if req.requested_by == ctx.user.id:
        raise _err(
            "CANNOT_DECIDE_OWN_REQUEST",
            "A different Super Admin has to answer your request. You can cancel it instead.",
            422,
        )
    if req.designated_approver is not None and req.designated_approver != ctx.user.id:
        raise _err(
            "NOT_DESIGNATED_APPROVER",
            "This request was sent to a different Super Admin.",
            403,
        )
    staff = _locked_staff(db, {ctx.user.id, req.requested_by})
    me = staff.get(ctx.user.id)
    if me is None or me.role != StaffRole.super_admin:  # revoked or demoted since the guard ran
        raise HTTPException(status_code=404, detail="Not Found")
    requester = staff.get(req.requested_by)
    if approve and (
        requester is None
        or requester.role != StaffRole.super_admin
        or not _active_super_admins(db, only=req.requested_by)
    ):
        raise _err(
            "REQUESTER_NOT_ACTIVE",
            "The person who asked is no longer an active Super Admin, so this can't run.",
            409,
        )
    # Run first: it can refuse (maintenance already on) and must then leave the request pending.
    outcome = _run_action(db, req, ctx, ip, user_agent) if approve else None
    req.status = ApprovalStatus.approved if approve else ApprovalStatus.rejected
    req.decided_by = ctx.user.id
    req.decided_at = datetime.now(UTC)
    req.decision_reason = reason
    req.outcome = outcome
    _audit_request(
        db,
        "approval.approved" if approve else "approval.rejected",
        req,
        actor=ctx.user.id,
        reason=reason,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return _serialize(db, [req])[0]


def cancel(
    db: Session,
    ctx: StaffContext,
    request_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    _require_active_actor(db, ctx)
    req = _load_pending(db, request_id)
    if req.requested_by != ctx.user.id:
        raise _err("NOT_YOUR_REQUEST", "Only the person who asked can cancel a request.", 403)
    req.status = ApprovalStatus.cancelled
    req.decided_by = ctx.user.id
    req.decided_at = datetime.now(UTC)
    req.decision_reason = reason
    _audit_request(
        db,
        "approval.cancelled",
        req,
        actor=ctx.user.id,
        reason=reason,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return _serialize(db, [req])[0]


def disable_maintenance(
    db: Session, ctx: StaffContext, *, reason: str, ip: str | None, user_agent: str | None
) -> dict[str, Any]:
    new = state.set_maintenance(db, enabled=False, message=None, actor_id=ctx.user.id)
    if new is None:
        raise _err("MAINTENANCE_NOT_ON", "Maintenance mode isn't on.", 409)
    write_audit(
        db,
        "maintenance.disabled",
        actor_user_id=ctx.user.id,
        entity_type="platform_setting",
        reason=reason,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return new
