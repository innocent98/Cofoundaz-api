import uuid
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

from app.db.models.enums import StaffRole

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


class _OverrideBase(BaseModel):
    """Every billing override: why, the staff member's own fresh code, and a caller-made id that
    makes a retry safe (the same `request_id` twice applies once)."""

    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    reason: Reason
    totp_code: TotpCode


class RefundRequest(_OverrideBase):
    invoice_id: uuid.UUID
    amount_minor: Annotated[int, Field(ge=1, le=2_000_000_000)]  # kobo


class CompleteRefundRequest(BaseModel):
    """Confirms a recorded refund was made in Paystack. Idempotent by nature: completing twice
    changes nothing, so it needs no request id."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason
    totp_code: TotpCode


class CreditRequest(_OverrideBase):
    credits: Annotated[int, Field(ge=1, le=100_000)]  # AI credits (1 credit = 50 tokens)


class ExtendTrialRequest(_OverrideBase):
    days: Annotated[int, Field(ge=1, le=60)]


class CompRequest(_OverrideBase):
    plan_key: Annotated[str, StringConstraints(min_length=1, max_length=20)]
    days: Annotated[int, Field(ge=1, le=365)]
