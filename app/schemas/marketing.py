import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus


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
