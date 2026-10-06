import datetime as dt
import uuid

from pydantic import BaseModel, Field, field_serializer, model_validator

from app.db.models.enums import CalendarEventType, RoadmapStatus

MAX_DESCRIPTION = 2000


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

    @model_validator(mode="after")
    def _times_make_sense(self) -> "EventCreate":
        _check_times(self.all_day, self.start_time, self.end_time)
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    type: CalendarEventType | None = None
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    all_day: bool | None = None
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)

    @model_validator(mode="after")
    def _no_explicit_null_on_required(self) -> "EventUpdate":
        # title/type/event_date/all_day are NOT NULL on the row, so an explicit null would hit a
        # NOT NULL violation at flush (500); reject it as 422 here instead. start_time, end_time
        # and description ARE nullable, so an explicit null legitimately CLEARS them — that is how
        # a caller un-sets a time. Omitting any field remains a no-op.
        for name in ("title", "type", "event_date", "all_day"):
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
