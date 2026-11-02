"""Staff Users console: search, profile, and the four support actions.

D-ADM-9: staff never reach documents, journal or AI chats. Every query here names the columns it
reads (no `select *`, no ORM objects serialized whole), and no secret column (password hash, MFA
secret, token hashes) is ever selected. Reads never write.
"""

import base64
import binascii
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.errors import AppError, EmailDeliveryFailed
from app.core.logger import log
from app.core.redis import get_redis
from app.db.models.auth import AuthSession
from app.db.models.billing import BillingSubscription
from app.db.models.enums import AuthTokenPurpose, MfaType, UserStatus
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.audit import write_audit
from app.platform.email import get_email_sender
from app.services.auth.emails import password_reset_email, verification_email
from app.services.auth.sessions import revoke_all_for_user
from app.services.auth.tokens import invalidate_unconsumed_tokens, issue_auth_token
from app.services.staff.like import contains_pattern

DORMANT_AFTER = timedelta(days=30)
RESET_TTL = timedelta(hours=1)
VERIFY_TTL = timedelta(hours=24)
EMAIL_COOLDOWN_SECONDS = 60
SESSIONS_SHOWN = 10


def _err(code: str, message: str, status: int) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def _billing_status(sub: Any) -> str:
    return sub.status if sub is not None else "free"


def _plan_key(sub: Any) -> str:
    return sub.plan_key if sub is not None else "starter"


# ---------------------------------------------------------------- list


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


def _primary_memberships(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    """Each user's primary workspace (their own founder workspace first, else the oldest
    membership) with its plan and billing status, plus how many workspaces they belong to."""
    if not user_ids:
        return {}
    rows = (
        db.query(
            Membership.user_id,
            Membership.startup_id,
            Membership.role,
            Startup.name,
            BillingSubscription.plan_key,
            BillingSubscription.status,
            BillingSubscription.comped,
        )
        .join(Startup, Startup.id == Membership.startup_id)
        .outerjoin(BillingSubscription, BillingSubscription.startup_id == Startup.id)
        .filter(
            Membership.user_id.in_(user_ids),
            Membership.status == MembershipStatus.active,
            Startup.deleted_at.is_(None),
        )
        .order_by(Membership.created_at.asc(), Membership.id.asc())
        .all()
    )
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for uid, sid, role, name, plan, status, comped in rows:
        entry = out.setdefault(uid, {"count": 0, "primary": None})
        entry["count"] += 1
        cand = {
            "workspace_id": str(sid),
            "workspace_name": name,
            "role": role.value,
            "plan_key": plan or "starter",
            "billing_status": status or "free",
            "comped": bool(comped),
        }
        if entry["primary"] is None or (
            role.value == "founder" and entry["primary"]["role"] != "founder"
        ):
            entry["primary"] = cand
    return out


def _activity(last_login_at: datetime | None) -> str:
    if last_login_at is None:
        return "dormant"
    return "active" if datetime.now(UTC) - last_login_at <= DORMANT_AFTER else "dormant"


def search_users(
    db: Session,
    *,
    query: str | None,
    billing_status: str | None,
    activity: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    q = (
        db.query(
            User.id,
            User.email,
            User.status,
            User.email_verified_at,
            User.created_at,
            User.last_login_at,
            UserProfile.full_name,
        )
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(User.deleted_at.is_(None))
    )
    if query:
        term = query.strip()
        pat = contains_pattern(term)
        conds: list[ColumnElement[bool]] = [
            User.email.ilike(pat, escape="\\"),
            UserProfile.full_name.ilike(pat, escape="\\"),
            User.id.in_(
                db.query(Membership.user_id)
                .join(Startup, Startup.id == Membership.startup_id)
                .filter(Startup.name.ilike(pat, escape="\\"), Startup.deleted_at.is_(None))
            ),
        ]
        try:
            ident = uuid.UUID(term)
        except ValueError:
            ident = None
        if ident is not None:
            conds.append(User.id == ident)
            conds.append(
                User.id.in_(db.query(Membership.user_id).filter(Membership.startup_id == ident))
            )
        q = q.filter(or_(*conds))
    if billing_status:
        sub_users = (
            db.query(Membership.user_id)
            .join(BillingSubscription, BillingSubscription.startup_id == Membership.startup_id)
            .filter(
                Membership.status == MembershipStatus.active,
                BillingSubscription.status == billing_status,
            )
        )
        if billing_status == "free":
            # a workspace with no subscription row is free too
            no_sub = (
                db.query(Membership.user_id)
                .outerjoin(
                    BillingSubscription, BillingSubscription.startup_id == Membership.startup_id
                )
                .filter(BillingSubscription.id.is_(None))
            )
            q = q.filter(or_(User.id.in_(sub_users), User.id.in_(no_sub)))
        else:
            q = q.filter(User.id.in_(sub_users))
    cutoff = datetime.now(UTC) - DORMANT_AFTER
    if activity == "active":
        q = q.filter(User.last_login_at >= cutoff)
    elif activity == "dormant":
        q = q.filter(or_(User.last_login_at.is_(None), User.last_login_at < cutoff))
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter((User.created_at < c_ts) | ((User.created_at == c_ts) & (User.id < c_id)))
    rows = q.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id)
    ws = _primary_memberships(db, [r.id for r in rows])
    items = []
    for r in rows:
        w = ws.get(r.id, {"count": 0, "primary": None})
        items.append(
            {
                "id": str(r.id),
                "email": r.email,
                "full_name": r.full_name,
                "status": r.status.value,
                "email_verified": r.email_verified_at is not None,
                "created_at": r.created_at.isoformat(),
                "last_login_at": r.last_login_at.isoformat() if r.last_login_at else None,
                "activity": _activity(r.last_login_at),
                "workspace_count": w["count"],
                "primary_workspace": w["primary"],
            }
        )
    return items, next_cursor


# ---------------------------------------------------------------- profile


def _staff_row(db: Session, user_id: uuid.UUID) -> PlatformStaff | None:
    return (
        db.query(PlatformStaff)
        .filter(PlatformStaff.user_id == user_id, PlatformStaff.revoked_at.is_(None))
        .first()
    )


def _can(
    ctx: StaffContext, target_id: uuid.UUID, status: UserStatus, is_staff: bool
) -> dict[str, bool]:
    blocked = target_id == ctx.user.id or (is_staff and not ctx.is_super_admin)
    return {
        "suspend": not blocked and status != UserStatus.disabled,
        "unsuspend": not blocked and status == UserStatus.disabled,
        "reset_password": not blocked and status in (UserStatus.active, UserStatus.locked),
        "resend_verification": not blocked and status == UserStatus.pending_verification,
    }


def user_profile(db: Session, ctx: StaffContext, user_id: uuid.UUID) -> dict[str, Any]:
    row = (
        db.query(
            User.id,
            User.email,
            User.status,
            User.email_verified_at,
            User.mfa_type,
            User.created_at,
            User.last_login_at,
            UserProfile.full_name,
            UserProfile.role_title,
            UserProfile.country,
            UserProfile.phone,
        )
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if row is None:
        raise _err("USER_NOT_FOUND", "No such user.", 404)
    workspaces = [
        {
            "workspace_id": str(sid),
            "name": name,
            "role": role.value,
            "membership_status": mstatus.value,
            "plan_key": plan or "starter",
            "billing_status": bstatus or "free",
            "comped": bool(comped),
            "trial_ends_at": trial.isoformat() if trial else None,
            "current_period_end": period.isoformat() if period else None,
            "cancel_at_period_end": bool(cancel),
        }
        for sid, name, role, mstatus, plan, bstatus, comped, trial, period, cancel in (
            db.query(
                Startup.id,
                Startup.name,
                Membership.role,
                Membership.status,
                BillingSubscription.plan_key,
                BillingSubscription.status,
                BillingSubscription.comped,
                BillingSubscription.trial_ends_at,
                BillingSubscription.current_period_end,
                BillingSubscription.cancel_at_period_end,
            )
            .join(Membership, Membership.startup_id == Startup.id)
            .outerjoin(BillingSubscription, BillingSubscription.startup_id == Startup.id)
            .filter(Membership.user_id == user_id, Startup.deleted_at.is_(None))
            .order_by(Membership.created_at.asc(), Membership.id.asc())
            .all()
        )
    ]
    sessions = [
        {
            "id": str(sid),
            "ip": ip,
            "user_agent": ua,
            "signed_in_at": created.isoformat(),
            "expires_at": expires.isoformat(),
            "revoked_at": revoked.isoformat() if revoked else None,
        }
        for sid, ip, ua, created, expires, revoked in (
            db.query(
                AuthSession.id,
                AuthSession.ip,
                AuthSession.user_agent,
                AuthSession.created_at,
                AuthSession.expires_at,
                AuthSession.revoked_at,
            )
            .filter(AuthSession.user_id == user_id)
            .order_by(AuthSession.created_at.desc())
            .limit(SESSIONS_SHOWN)
            .all()
        )
    ]
    staff = _staff_row(db, user_id)
    # A staff member's sign-in places and phone are not for an Admin's eyes (P2).
    hide = staff is not None and not ctx.is_super_admin
    if hide:
        sessions = []
    return {
        "id": str(row.id),
        "email": row.email,
        "full_name": row.full_name,
        "role_title": row.role_title,
        "country": row.country,
        "phone": None if hide else row.phone,
        "status": row.status.value,
        "email_verified": row.email_verified_at is not None,
        "two_factor": row.mfa_type == MfaType.totp,
        "created_at": row.created_at.isoformat(),
        "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        "activity": _activity(row.last_login_at),
        "is_staff": staff is not None,
        "workspaces": workspaces,
        "sessions": sessions,
        "can": _can(ctx, row.id, row.status, staff is not None),
    }


# ---------------------------------------------------------------- actions


def _lock_target(db: Session, ctx: StaffContext, user_id: uuid.UUID) -> User:
    """The target, row-locked. A staff member acts on themselves never, and on other staff only
    as a Super Admin (P2): suspending or resetting a colleague is a Super Admin's call."""
    user = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .with_for_update()
        .first()
    )
    if user is None:
        raise _err("USER_NOT_FOUND", "No such user.", 404)
    if user.id == ctx.user.id:
        raise _err("CANNOT_ACT_ON_SELF", "You can't do that to your own account.", 422)
    if _staff_row(db, user.id) is not None and not ctx.is_super_admin:
        raise _err("STAFF_TARGET", "Only a Super Admin can act on a staff account.", 403)
    return user


def _audit(
    db: Session,
    ctx: StaffContext,
    action: str,
    user: User,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> None:
    write_audit(
        db,
        action,
        actor_user_id=ctx.user.id,
        entity_type="user",
        entity_id=user.id,
        reason=reason,
        details={"email": user.email},
        ip=ip,
        user_agent=ua,
    )


def suspend_user(
    db: Session,
    ctx: StaffContext,
    user_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    user = _lock_target(db, ctx, user_id)
    if user.status == UserStatus.disabled:
        raise _err("ALREADY_SUSPENDED", "That account is already suspended.", 409)
    user.status = UserStatus.disabled
    sessions = revoke_all_for_user(db, user.id)
    # A reset or verification link issued before the suspension must not outlive it.
    for purpose in AuthTokenPurpose:
        invalidate_unconsumed_tokens(db, user, purpose)
    _audit(db, ctx, "user.suspended", user, reason, ip, ua)
    db.commit()
    return {"id": str(user.id), "status": user.status.value, "sessions_revoked": sessions}


def unsuspend_user(
    db: Session,
    ctx: StaffContext,
    user_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    user = _lock_target(db, ctx, user_id)
    if user.status != UserStatus.disabled:
        raise _err("NOT_SUSPENDED", "That account isn't suspended.", 409)
    # Back to what they were: someone who never verified their email goes back to verifying.
    user.status = (
        UserStatus.active if user.email_verified_at is not None else UserStatus.pending_verification
    )
    _audit(db, ctx, "user.unsuspended", user, reason, ip, ua)
    db.commit()
    return {"id": str(user.id), "status": user.status.value}


def _claim_email_slot(kind: str, user_id: uuid.UUID) -> None:
    if not get_redis().set(
        f"staff_user_email:{kind}:{user_id}", "1", nx=True, ex=EMAIL_COOLDOWN_SECONDS
    ):
        raise _err("TOO_SOON", "An email was just sent to that person. Wait a minute.", 429)


def send_password_reset(
    db: Session,
    ctx: StaffContext,
    user_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    user = _lock_target(db, ctx, user_id)
    if user.status not in (UserStatus.active, UserStatus.locked):
        raise _err(
            "RESET_NOT_ALLOWED",
            "A password reset can only be sent to an active account.",
            409,
        )
    _claim_email_slot("reset", user.id)
    try:
        with db.begin_nested():  # a failed send rolls the token back
            invalidate_unconsumed_tokens(db, user, AuthTokenPurpose.password_reset)
            raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, RESET_TTL)
            get_email_sender().send(password_reset_email(user.email, raw))
    except EmailDeliveryFailed:
        log.warning("staff password reset email failed: provider refused the send")
        get_redis().delete(f"staff_user_email:reset:{user.id}")
        raise
    _audit(db, ctx, "user.password_reset_sent", user, reason, ip, ua)
    db.commit()
    return {"id": str(user.id), "sent": True}


def resend_verification(
    db: Session,
    ctx: StaffContext,
    user_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    user = _lock_target(db, ctx, user_id)
    if user.status != UserStatus.pending_verification:
        raise _err("ALREADY_VERIFIED", "That account has already verified its email.", 409)
    _claim_email_slot("verify", user.id)
    try:
        with db.begin_nested():
            invalidate_unconsumed_tokens(db, user, AuthTokenPurpose.email_verification)
            raw = issue_auth_token(db, user, AuthTokenPurpose.email_verification, VERIFY_TTL)
            get_email_sender().send(verification_email(user.email, raw))
    except EmailDeliveryFailed:
        log.warning("staff verification email failed: provider refused the send")
        get_redis().delete(f"staff_user_email:verify:{user.id}")
        raise
    _audit(db, ctx, "user.verification_resent", user, reason, ip, ua)
    db.commit()
    return {"id": str(user.id), "sent": True}
