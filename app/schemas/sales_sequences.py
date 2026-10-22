import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models.enums import EnrollmentStatus, SequenceStatus

MAX_STEPS = 10
MAX_WAIT_DAYS = 60


class StepIn(BaseModel):
    wait_days: int = Field(ge=0, le=MAX_WAIT_DAYS)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("subject", "body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("This field is required.")
        return v.strip()


def _clean_name(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("Name is required.")
    return v


class SequenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    steps: list[StepIn] = Field(min_length=1, max_length=MAX_STEPS)
    stop_on_stage_change: bool = False

    _name = field_validator("name")(_clean_name)


class SequenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    steps: list[StepIn] | None = Field(default=None, min_length=1, max_length=MAX_STEPS)
    stop_on_stage_change: bool | None = None
    status: SequenceStatus | None = None

    _name = field_validator("name")(_clean_name)

    @model_validator(mode="after")
    def _no_nulls(self) -> "SequenceUpdate":
        for f in ("name", "steps", "stop_on_stage_change", "status"):
            if f in self.model_fields_set and getattr(self, f) is None:
                raise ValueError(f"{f} cannot be null.")
        return self


class EnrollIn(BaseModel):
    lead_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    contact_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _something(self) -> "EnrollIn":
        if not self.lead_ids and not self.contact_ids:
            raise ValueError("Pick at least one lead or contact.")
        return self


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence_id: uuid.UUID
    lead_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    email: str
    first_name: str
    last_name: str
    company: str
    sender_name: str
    sender_email: str
    status: EnrollmentStatus
    stopped_reason: str | None
    current_step: int
    next_send_at: datetime | None
    created_at: datetime


class StepWriteIn(BaseModel):
    instructions: str | None = Field(default=None, max_length=500)


class FollowUpSend(BaseModel):
    draft_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("subject", "body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("This field is required.")
        return v.strip()


SequenceDict = dict[str, Any]
