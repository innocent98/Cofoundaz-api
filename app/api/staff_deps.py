"""Guards for the staff-only routers (`/admin`, `/super-admin`).

The role is read from `platform_staff` on every request: the JWT carries only `sub` and `exp`, so a
revoke (or MFA turned off) applies on the caller's next call. A caller who is not active staff with
TOTP on gets the same 404 as an unknown route, so these routes don't reveal themselves. Only active
staff who lack the scope (never a Super Admin) see a 403.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.core.errors import Forbidden, NotFound
from app.db.models.enums import MfaType, StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.user import User
from app.db.session import get_db


@dataclass(frozen=True)
class StaffContext:
    user: User
    staff: PlatformStaff

    @property
    def is_super_admin(self) -> bool:
        return self.staff.role == StaffRole.super_admin


def _staff_context(db: Session, user: User | None) -> StaffContext:
    if (
        user is None
        or user.email_verified_at is None
        or user.status != UserStatus.active
        or user.mfa_type != MfaType.totp
        or not user.mfa_secret
    ):
        raise NotFound()
    staff = (
        db.query(PlatformStaff)
        .filter(PlatformStaff.user_id == user.id, PlatformStaff.revoked_at.is_(None))
        .first()
    )
    if staff is None:
        raise NotFound()
    return StaffContext(user=user, staff=staff)


def require_staff(*scopes: str) -> Callable[..., StaffContext]:
    """Active staff. With scopes, an Admin needs at least one of them; a Super Admin passes all."""

    def _dep(
        user: User | None = Depends(get_optional_user),  # noqa: B008
        db: Session = Depends(get_db),  # noqa: B008
    ) -> StaffContext:
        ctx = _staff_context(db, user)
        if scopes and not ctx.is_super_admin and not set(scopes) & set(ctx.staff.scopes):
            raise Forbidden()
        return ctx

    return _dep


def require_super_admin(
    user: User | None = Depends(get_optional_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> StaffContext:
    ctx = _staff_context(db, user)
    if not ctx.is_super_admin:
        raise NotFound()
    return ctx
