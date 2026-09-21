import uuid
from datetime import date as _date

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import JournalMood


class JournalEntryCreate(BaseModel):
    date: _date
    content: str = Field(min_length=1)
    mood: JournalMood
    stress: int = Field(ge=1, le=10)

    @field_validator("date")
    @classmethod
    def reject_future_dates(cls, value: _date) -> _date:
        if value > _date.today():
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
    first_line: str


class JournalEntryListResponse(BaseModel):
    entries: list[JournalEntryListItem]
    total: int


class MoodDataPoint(BaseModel):
    date: _date
    mood: JournalMood
    stress: int


class MoodTrendResponse(BaseModel):
    points: list[MoodDataPoint]


class JournalPromptResponse(BaseModel):
    prompt: str
