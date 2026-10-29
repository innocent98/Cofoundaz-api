import uuid
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import JournalMood


class JournalEntryCreate(BaseModel):
    date: _date
    content: str = Field(min_length=1)
    mood: JournalMood
    stress: int = Field(ge=1, le=10)

    @field_validator("date")
    @classmethod
    def reject_future_dates(cls, value: _date) -> _date:
        # One day of slack: the client sends its own local calendar date, which can be a day
        # ahead of the server's UTC date (e.g. Lagos just after midnight).
        if value > _date.today() + timedelta(days=1):
            raise ValueError("Entry date cannot be in the future.")
        return value

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content cannot be blank.")
        return value


class JournalEntryUpdate(BaseModel):
    content: str | None = None
    mood: JournalMood | None = None
    stress: int | None = Field(default=None, ge=1, le=10)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Content cannot be blank.")
        return value


class JournalEntryResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    founder_id: uuid.UUID
    date: _date
    content: str
    mood: JournalMood
    stress: int


class JournalEntryListItem(BaseModel):
    id: uuid.UUID
    date: _date
    mood: JournalMood
    stress: int
    first_line: str
    preview: str
    word_count: int


class JournalEntryListResponse(BaseModel):
    entries: list[JournalEntryListItem]
    total: int


class MoodDataPoint(BaseModel):
    date: _date
    mood: JournalMood
    stress: int


class MoodAnnotation(BaseModel):
    date: _date
    label: str


class MoodTrendResponse(BaseModel):
    points: list[MoodDataPoint]
    annotations: list[MoodAnnotation] = []


class JournalPromptResponse(BaseModel):
    prompt: str
    dismissed: bool = False


class JournalMonthCount(BaseModel):
    month: str
    count: int


class JournalMonthsResponse(BaseModel):
    months: list[JournalMonthCount]


class JournalSettingsResponse(BaseModel):
    passcode_enabled: bool
    locked_until: datetime | None
    daily_prompt_notifications: bool
    evening_reminder: bool


class JournalSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_prompt_notifications: bool | None = None
    evening_reminder: bool | None = None


class PasscodeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passcode: str = Field(min_length=4, max_length=12)
    current_passcode: str | None = Field(default=None, max_length=12)

    @field_validator("passcode")
    @classmethod
    def digits_only(cls, value: str) -> str:
        if not value.isascii() or not value.isdigit():
            raise ValueError("Use 4 to 12 digits.")
        return value


class PasscodeUnlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passcode: str = Field(min_length=1, max_length=12)


class PasscodeRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=256)


class UnlockResponse(BaseModel):
    token: str
    expires_in: int


class JournalDeleteAll(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal["DELETE"]


class RetroBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    what_worked: str = Field(default="", max_length=4000)
    what_didnt: str = Field(default="", max_length=4000)
    what_change: str = Field(default="", max_length=4000)


class RetroCreate(RetroBody):
    milestone_id: uuid.UUID
