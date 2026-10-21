import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import StageKind


class StageIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    kind: StageKind
    win_probability: int = Field(ge=0, le=100)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required.")
        return v


class StagesReplace(BaseModel):
    stages: list[StageIn]


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    position: int
    kind: StageKind
    win_probability: int


class SettingsUpdate(BaseModel):
    stall_days: int | None = Field(default=None, ge=3, le=90)
    outreach_postal_address: str | None = Field(default=None, max_length=500)


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stall_days: int
    outreach_postal_address: str | None
