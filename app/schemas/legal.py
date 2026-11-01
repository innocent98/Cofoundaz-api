import datetime as dt
from typing import Annotated

from pydantic import BaseModel, StringConstraints, model_validator

from app.db.models.enums import IpAssetStatus, IpAssetType

# Trimmed first, then length-checked, so "   " is rejected rather than stored as blank.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Jurisdiction = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class FormationStepPatch(BaseModel):
    """`PATCH /legal/formation/steps/{step_key}` — the desired state, not a delta.

    The FE sends where it wants the tick to be, so a repeat is idempotent (spec D7).
    """

    jurisdiction: str
    completed: bool


class IpAssetCreate(BaseModel):
    type: IpAssetType
    name: Name
    # A bounded free string, not a closed set: it must hold "Global" and "EU" (spec D10).
    jurisdiction: Jurisdiction
    # Three values only. `expired` is derived from renewal_on and cannot be sent (spec D9).
    status: IpAssetStatus
    renewal_on: dt.date | None = None


class IpAssetUpdate(BaseModel):
    type: IpAssetType | None = None
    name: Name | None = None
    jurisdiction: Jurisdiction | None = None
    status: IpAssetStatus | None = None
    renewal_on: dt.date | None = None

    @model_validator(mode="after")
    def _no_explicit_null_on_required(self) -> "IpAssetUpdate":
        # type/name/jurisdiction/status are NOT NULL on the row, so an explicit null would hit a
        # NOT NULL violation at flush (500); reject it as 422 here instead. `renewal_on` IS
        # nullable, so an explicit null legitimately CLEARS it. Omitting a field is a no-op.
        for field in ("type", "name", "jurisdiction", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} may not be null")
        return self
