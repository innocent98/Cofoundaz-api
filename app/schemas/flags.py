import uuid
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.db.models.enums import FlagStage
from app.schemas.staff import Reason, TotpCode

FlagKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,59}$")]
FlagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
FlagDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
RolloutPct = Annotated[int, Field(ge=0, le=100)]
Allowlist = Annotated[list[uuid.UUID], Field(max_length=200)]


class FlagCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: FlagKey
    name: FlagName
    description: FlagDescription = ""
    stage: FlagStage
    enabled: bool = False
    rollout_pct: RolloutPct = 0
    workspace_allowlist: Allowlist = []
    reason: Reason
    totp_code: TotpCode


class FlagUpdateRequest(BaseModel):
    """Send only the fields that change. The key never changes."""

    model_config = ConfigDict(extra="forbid")

    name: FlagName | None = None
    description: FlagDescription | None = None
    stage: FlagStage | None = None
    enabled: bool | None = None
    rollout_pct: RolloutPct | None = None
    workspace_allowlist: Allowlist | None = None
    reason: Reason
    totp_code: TotpCode

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        # An omitted field means "unchanged"; an explicit null is a mistake, not a way to clear.
        for field in self.model_fields_set - {"reason", "totp_code"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} can't be null; leave it out to keep it.")
        return self
