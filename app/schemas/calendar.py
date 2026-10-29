import datetime as dt
import uuid
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_serializer, field_validator, model_validator

from app.db.models.enums import CalendarEventType, RoadmapStatus

MAX_DESCRIPTION = 2000
MAX_ATTENDEES = 50
DEFAULT_TZ = "Africa/Lagos"


def _check_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("timezone must be an IANA name such as Africa/Lagos") from exc
    return value


def _check_url(value: str | None) -> str | None:
    if value is not None and not value.lower().startswith(("https://", "http://")):
        raise ValueError("video_url must start with http:// or https://")
    return value


def _check_dates(event_date: dt.date | None, end_date: dt.date | None) -> None:
    if event_date is not None and end_date is not None and end_date < event_date:
        raise ValueError("end_date may not be earlier than event_date")


def _check_times(all_day: bool, start_time: dt.time | None, end_time: dt.time | None) -> None:
    """Shared by create and update so a PATCH cannot leave a row in a state a POST would refuse.

    Update calls this against the MERGED values, never the payload alone.
    """
    if all_day and (start_time is not None or end_time is not None):
        # Refuse rather than silently discard what the caller sent (spec D9).
        raise ValueError("an all-day event may not carry a start or end time")
    if end_time is not None and start_time is None:
        raise ValueError("end_time needs a start_time")
    if start_time is not None and end_time is not None and end_time < start_time:
        raise ValueError("end_time may not be earlier than start_time")


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: CalendarEventType
    event_date: dt.date
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    all_day: bool = False
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    end_date: dt.date | None = None
    timezone: str = DEFAULT_TZ
    location: str | None = Field(default=None, max_length=300)
    video_url: str | None = Field(default=None, max_length=500)
    visibility: Literal["team", "private"] = "team"

    _tz = field_validator("timezone")(_check_tz)
    _url = field_validator("video_url")(_check_url)

    @model_validator(mode="after")
    def _times_make_sense(self) -> "EventCreate":
        _check_times(self.all_day, self.start_time, self.end_time)
        _check_dates(self.event_date, self.end_date)
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    type: CalendarEventType | None = None
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    all_day: bool | None = None
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    end_date: dt.date | None = None
    timezone: str | None = None
    location: str | None = Field(default=None, max_length=300)
    video_url: str | None = Field(default=None, max_length=500)
    visibility: Literal["team", "private"] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_tz(v)

    _url = field_validator("video_url")(_check_url)

    @model_validator(mode="after")
    def _no_explicit_null_on_required(self) -> "EventUpdate":
        # title/type/event_date/all_day are NOT NULL on the row, so an explicit null would hit a
        # NOT NULL violation at flush (500); reject it as 422 here instead. start_time, end_time
        # and description ARE nullable, so an explicit null legitimately CLEARS them — that is how
        # a caller un-sets a time. Omitting any field remains a no-op.
        for name in ("title", "type", "event_date", "all_day", "timezone", "visibility"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class EventResponse(BaseModel):
    id: uuid.UUID
    title: str
    type: CalendarEventType
    event_date: dt.date
    start_time: dt.time | None
    end_time: dt.time | None
    all_day: bool
    description: str | None
    end_date: dt.date | None
    timezone: str
    location: str | None
    video_url: str | None
    visibility: str
    created_by: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime

    @field_serializer("start_time", "end_time")
    def _as_hhmm(self, value: dt.time | None) -> str | None:
        # D6 and the FE guide promise HH:MM. Without this, model_dump(mode="json") emits
        # HH:MM:SS. Kept here rather than at each call site so it cannot be forgotten.
        return None if value is None else value.isoformat(timespec="minutes")


class MilestoneResponse(BaseModel):
    id: uuid.UUID
    title: str
    due_on: dt.date | None
    status: RoadmapStatus
    calendar_status: str
    progress: int
    days_left: int | None
    tasks_done: int
    tasks_total: int


class AttendeeIn(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)


class AttendeeOut(BaseModel):
    email: str
    name: str | None
    user_id: uuid.UUID | None
    internal: bool
    invite_state: str


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    event_date: dt.date
    end_date: dt.date | None = None
    start_time: dt.time
    end_time: dt.time | None = None
    timezone: str = DEFAULT_TZ
    location: str | None = Field(default=None, max_length=300)
    video_url: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    agenda: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    attendees: list[AttendeeIn] = Field(default_factory=list, max_length=MAX_ATTENDEES)
    assistant_enabled: bool = True
    visibility: Literal["team", "private"] = "team"

    _tz = field_validator("timezone")(_check_tz)
    _url = field_validator("video_url")(_check_url)

    @model_validator(mode="after")
    def _ok(self) -> "MeetingCreate":
        _check_times(False, self.start_time, self.end_time)
        _check_dates(self.event_date, self.end_date)
        return self


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    event_date: dt.date | None = None
    end_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    timezone: str | None = None
    location: str | None = Field(default=None, max_length=300)
    video_url: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    agenda: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    attendees: list[AttendeeIn] | None = Field(default=None, max_length=MAX_ATTENDEES)
    assistant_enabled: bool | None = None
    visibility: Literal["team", "private"] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_tz(v)

    _url = field_validator("video_url")(_check_url)

    @model_validator(mode="after")
    def _no_null_required(self) -> "MeetingUpdate":
        for name in (
            "title",
            "event_date",
            "start_time",
            "timezone",
            "attendees",
            "visibility",
            "assistant_enabled",
        ):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class AssistantIn(BaseModel):
    transcript: str = Field(min_length=20, max_length=60000)


class ActionItemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    assignee_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _no_null_title(self) -> "ActionItemPatch":
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title may not be null")
        return self


class AddToTasksIn(BaseModel):
    item_ids: list[str] | None = None  # None means every item not yet added
    milestone_id: uuid.UUID | None = None


class ReminderRuleIn(BaseModel):
    offsets_minutes: list[int] = Field(min_length=1, max_length=6)

    @field_validator("offsets_minutes")
    @classmethod
    def _offsets(cls, v: list[int]) -> list[int]:
        if any(o < 1 or o > 14 * 24 * 60 for o in v):
            raise ValueError("each offset must be between 1 minute and 14 days")
        if len(set(v)) != len(v):
            raise ValueError("offsets may not repeat")
        return sorted(v, reverse=True)


class SyncSettingsIn(BaseModel):
    direction: Literal["two_way", "pull_only", "push_only"] | None = None
    calendar_id: str | None = Field(default=None, min_length=1, max_length=200)
