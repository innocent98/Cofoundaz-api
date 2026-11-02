import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.db.models.enums import ReportItemType, ReportReason
from app.schemas.staff import Reason, TotpCode

Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportCreateRequest(_Strict):
    item_type: ReportItemType
    item_id: uuid.UUID
    reason: ReportReason
    note: Note | None = None

    @model_validator(mode="after")
    def _other_needs_a_note(self) -> "ReportCreateRequest":
        if self.reason == ReportReason.other and not self.note:
            raise ValueError("Tell us what's wrong when the reason is 'other'.")
        return self


class ReasonRequest(_Strict):
    reason: Reason


class StepUpReasonRequest(_Strict):
    """Remove and restore are destructive: they carry the staff member's own fresh code."""

    reason: Reason
    totp_code: TotpCode
