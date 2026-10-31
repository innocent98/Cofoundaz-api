"""Make an existing account a Super Admin: `python -m app.scripts.create_super_admin <email>`.

The first Super Admin has no one to invite them, so this is run by hand on the server. The user
must already exist, be verified and active, and have an authenticator app turned on. Running it
for someone who is already a Super Admin is a no-op; a revoked or Admin row is promoted.
"""

import sys

from app.db.models.enums import MfaType, StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.user import User
from app.db.session import SessionLocal
from app.platform.audit import write_audit


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m app.scripts.create_super_admin <email>", file=sys.stderr)
        return 2
    email = argv[1].strip()
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
        if user is None:
            print(f"No account with email {email}. They must sign up first.", file=sys.stderr)
            return 1
        if user.email_verified_at is None or user.status != UserStatus.active:
            print("That account isn't verified and active.", file=sys.stderr)
            return 1
        if user.mfa_type != MfaType.totp:
            print("That account has no authenticator app. Turn on 2FA first.", file=sys.stderr)
            return 1
        row = db.query(PlatformStaff).filter(PlatformStaff.user_id == user.id).first()
        if row is not None and row.revoked_at is None and row.role == StaffRole.super_admin:
            print(f"{email} is already a Super Admin.")
            return 0
        if row is None:
            row = PlatformStaff(user_id=user.id, role=StaffRole.super_admin, scopes=[])
            db.add(row)
        else:
            row.role, row.scopes, row.revoked_at = StaffRole.super_admin, [], None
        db.flush()
        write_audit(
            db,
            "staff.bootstrapped",
            actor_user_id=None,
            entity_type="platform_staff",
            entity_id=row.id,
            reason="Created by the create_super_admin command on the server.",
            details={"email": user.email, "role": "super_admin"},
        )
        db.commit()
    print(f"{email} is now a Super Admin.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
