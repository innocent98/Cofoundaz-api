import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import (
    CampaignObjective,
    CampaignStatus,
    ChannelKey,
    ChannelStatus,
    ContentStatus,
)


class CalendarEntryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    channel: ChannelKey
    status: ContentStatus = ContentStatus.draft
    body: str | None = None
    media_ref: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Title cannot be blank.")
        return v


class CalendarEntryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    channel: ChannelKey | None = None
    status: ContentStatus | None = None
    body: str | None = None
    media_ref: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Title cannot be blank.")
        return v


class CalendarEntryResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    created_by: uuid.UUID | None
    title: str
    channel: ChannelKey
    status: ContentStatus
    body: str | None
    media_ref: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChannelUpdate(BaseModel):
    status: ChannelStatus | None = None
    notes: str | None = None


class ChannelResponse(BaseModel):
    id: uuid.UUID
    key: ChannelKey
    status: ChannelStatus
    notes: str | None


class OverviewResponse(BaseModel):
    scheduled_this_week: int
    active_channels: int
    active_campaigns: int | None = None
    top_channel_by_conversions: str | None = None
    ai_content_ideas: int | None = None


class SegmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: dict[str, Any] = Field(default_factory=dict)
    est_size: int | None = Field(default=None, ge=0)
    persona_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be blank.")
        return v


class SegmentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    definition: dict[str, Any] | None = None
    est_size: int | None = Field(default=None, ge=0)
    persona_id: uuid.UUID | None = None


class SegmentResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    name: str
    definition: dict[str, Any]
    est_size: int | None
    persona_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


def _validate_channel_mix(v: dict[str, float]) -> dict[str, float]:
    valid = {c.value for c in ChannelKey}
    for key, pct in v.items():
        if key not in valid:
            raise ValueError(f"Unknown channel: {key}")
        if not 0 <= pct <= 100:
            raise ValueError(f"channel_mix[{key}] must be between 0 and 100.")
    return v


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: CampaignObjective
    budget: int = Field(default=0, ge=0)
    channel_mix: dict[str, float] = Field(default_factory=dict)
    segment_ids: list[uuid.UUID] = Field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be blank.")
        return v

    @field_validator("channel_mix")
    @classmethod
    def _valid_channel_mix(cls, v: dict[str, float]) -> dict[str, float]:
        return _validate_channel_mix(v)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    objective: CampaignObjective | None = None
    budget: int | None = Field(default=None, ge=0)
    channel_mix: dict[str, float] | None = None
    status: CampaignStatus | None = None
    segment_ids: list[uuid.UUID] | None = None
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("channel_mix")
    @classmethod
    def _valid_channel_mix(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        return _validate_channel_mix(v)


class CampaignResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    name: str
    objective: CampaignObjective
    budget: int
    channel_mix: dict[str, float]
    status: CampaignStatus
    metrics: dict[str, Any]
    segment_ids: list[uuid.UUID]
    period_start: date | None
    period_end: date | None
    launched_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
