import uuid
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, model_validator

from app.db.models.enums import ApprovalAction, StaffRole

AdminScope = Literal["support", "content", "moderation"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]
TotpCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6}$")]


def _check_scopes(role: StaffRole, scopes: Sequence[str]) -> list[AdminScope]:
    scopes = sorted(set(scopes))
    if role == StaffRole.admin and not scopes:
        raise ValueError("An Admin needs at least one scope.")
    if role == StaffRole.super_admin and scopes:
        raise ValueError("A Super Admin has every scope; send none.")
    return scopes  # type: ignore[return-value]


class StaffInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: StaffRole
    scopes: list[AdminScope] = []
    reason: Reason
    totp_code: TotpCode

    @model_validator(mode="after")
    def _scopes_match_role(self) -> Self:
        self.scopes = _check_scopes(self.role, self.scopes)
        return self


class StaffUpdateRequest(BaseModel):
    """Change an active staff member's role or scopes without revoking them."""

    model_config = ConfigDict(extra="forbid")

    role: StaffRole
    scopes: list[AdminScope] = []
    reason: Reason
    totp_code: TotpCode

    @model_validator(mode="after")
    def _scopes_match_role(self) -> Self:
        self.scopes = _check_scopes(self.role, self.scopes)
        return self


class StaffRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason
    totp_code: TotpCode


class StaffActionRequest(BaseModel):
    """Body of every staff mutation on a user: why, and the staff member's own fresh code."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason
    totp_code: TotpCode


MaintenanceMessage = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)
]


class ApprovalCreateRequest(BaseModel):
    """Ask a different Super Admin to approve maintenance mode or a global sign-out."""

    model_config = ConfigDict(extra="forbid")

    action: ApprovalAction
    reason: Reason
    # What founders see while maintenance is on. Only for `maintenance_enable`.
    message: MaintenanceMessage | None = None
    # Send the request to one Super Admin; leave out for any other Super Admin to answer.
    approver_user_id: uuid.UUID | None = None
    totp_code: TotpCode
