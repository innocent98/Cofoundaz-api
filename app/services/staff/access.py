"""Staff list, invite and revoke (Super Admin). Callers have already passed the guard and step-up."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.errors import AppError
from app.db.models.enums import MfaType, StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.user import User, UserProfile
from app.platform.audit import write_audit


def _err(code: str, message: str, status: int) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def serialize_staff(db: Session, rows: list[PlatformStaff]) -> list[dict[str, Any]]:
    ids = {r.user_id for r in rows} | {r.invited_by for r in rows if r.invited_by}
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
            "user_id": str(r.user_id),
            "email": users.get(r.user_id, (None, None))[0],
            "full_name": users.get(r.user_id, (None, None))[1],
            "role": r.role.value,
            "scopes": list(r.scopes),
            "invited_by_email": users.get(r.invited_by, (None, None))[0] if r.invited_by else None,
            "created_at": r.created_at.isoformat(),
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        }
        for r in rows
    ]


def list_staff(db: Session, *, include_revoked: bool) -> list[dict[str, Any]]:
    q = db.query(PlatformStaff)
    if not include_revoked:
        q = q.filter(PlatformStaff.revoked_at.is_(None))
    rows = q.order_by(PlatformStaff.created_at.asc(), PlatformStaff.id.asc()).all()
    return serialize_staff(db, rows)


def _eligible_target(db: Session, email: str) -> User:
    user = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
    if user is None:
        raise _err("USER_NOT_FOUND", "No account with that email. They need to sign up first.", 404)
    if user.email_verified_at is None or user.status != UserStatus.active:
        raise _err("STAFF_ACCOUNT_NOT_ELIGIBLE", "That account isn't active and verified.", 422)
    if user.mfa_type != MfaType.totp:
        raise _err(
            "STAFF_MFA_REQUIRED",
            "Staff must have an authenticator app turned on. Ask them to enable it first.",
            422,
        )
    return user


def invite_staff(
    db: Session,
    ctx: StaffContext,
    *,
    email: str,
    role: StaffRole,
    scopes: Sequence[str],
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    target = _eligible_target(db, email)
    row = (
        db.query(PlatformStaff).filter(PlatformStaff.user_id == target.id).with_for_update().first()
    )
    reinstated = False
    if row is not None and row.revoked_at is None:
        raise _err("ALREADY_STAFF", "That person is already staff.", 409)
    if row is None:
        row = PlatformStaff(
            user_id=target.id, role=role, scopes=list(scopes), invited_by=ctx.user.id
        )
        db.add(row)
    else:
        reinstated = True
        row.role, row.scopes = role, list(scopes)
        row.invited_by, row.revoked_at = ctx.user.id, None
    db.flush()
    write_audit(
        db,
        "staff.reinstated" if reinstated else "staff.invited",
        actor_user_id=ctx.user.id,
        entity_type="platform_staff",
        entity_id=row.id,
        reason=reason,
        details={"email": target.email, "role": role.value, "scopes": list(scopes)},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return serialize_staff(db, [row])[0]


def revoke_staff(
    db: Session,
    ctx: StaffContext,
    *,
    staff_id: uuid.UUID,
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    # Lock every active Super Admin row (plus the target) so two concurrent revokes can't each
    # see "another Super Admin remains" and leave none.
    locked = (
        db.query(PlatformStaff)
        .filter(
            PlatformStaff.revoked_at.is_(None),
            (PlatformStaff.role == StaffRole.super_admin) | (PlatformStaff.id == staff_id),
        )
        .order_by(PlatformStaff.id)
        .with_for_update()
        .all()
    )
    row = next((r for r in locked if r.id == staff_id), None)
    if row is None:
        existing = db.query(PlatformStaff).filter(PlatformStaff.id == staff_id).first()
        if existing is not None:
            raise _err("ALREADY_REVOKED", "That person's staff access is already revoked.", 409)
        raise _err("STAFF_NOT_FOUND", "No such staff member.", 404)
    if row.user_id == ctx.user.id:
        raise _err("CANNOT_REVOKE_SELF", "You can't revoke your own access.", 422)
    others = [r for r in locked if r.role == StaffRole.super_admin and r.id != row.id]
    if row.role == StaffRole.super_admin and not others:
        raise _err("LAST_SUPER_ADMIN", "That is the last Super Admin; it can't be revoked.", 409)
    row.revoked_at = datetime.now(UTC)
    target = db.query(User.email).filter(User.id == row.user_id).scalar()
    write_audit(
        db,
        "staff.revoked",
        actor_user_id=ctx.user.id,
        entity_type="platform_staff",
        entity_id=row.id,
        reason=reason,
        details={"email": target, "role": row.role.value, "scopes": list(row.scopes)},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return serialize_staff(db, [row])[0]
