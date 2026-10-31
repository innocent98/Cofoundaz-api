from typing import Any

from fastapi import APIRouter, Depends

from app.api.staff_deps import StaffContext, require_staff
from app.core.envelope import success_response

router = APIRouter()


@router.get("/me")
def staff_me(ctx: StaffContext = Depends(require_staff())) -> dict[str, Any]:  # noqa: B008
    """Who the console thinks the caller is. 404 for anyone who is not active staff."""
    return success_response(
        {
            "user_id": str(ctx.user.id),
            "email": ctx.user.email,
            "role": ctx.staff.role.value,
            "scopes": list(ctx.staff.scopes),
        }
    )
