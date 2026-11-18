"""Who counts as platform staff outside the console's own guards (SO4).

Staff (an Admin or a Super Admin with an unrevoked `platform_staff` row) are not founders: they
never go through founder onboarding, never get a founder workspace, and anything they already own
is left out of the console KPIs. This deliberately ignores the TOTP and status checks in
`app.api.staff_deps`: a staff member who has not turned 2FA on yet is still not a founder.
"""

import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models.enums import StaffRole
from app.db.models.platform_staff import PlatformStaff


def active_staff_ids() -> Select[tuple[uuid.UUID]]:
    """User ids with an unrevoked staff row, as a subquery for `.in_()` / `.notin_()`."""
    return select(PlatformStaff.user_id).where(PlatformStaff.revoked_at.is_(None))


def staff_role(db: Session, user_id: uuid.UUID) -> StaffRole | None:
    row = (
        db.query(PlatformStaff)
        .filter(PlatformStaff.user_id == user_id, PlatformStaff.revoked_at.is_(None))
        .first()
    )
    return row.role if row else None
