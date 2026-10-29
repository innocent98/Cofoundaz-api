from datetime import time
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, field_validator

from app.services.notifications.categories import CATEGORIES


class PreferencesUpdate(BaseModel):
    master_email: bool | None = None
    categories: dict[str, bool] | None = None
    digest_mode: Literal["realtime", "daily", "weekly"] | None = None
    digest_time: time | None = None
    digest_day: int | None = Field(default=None, ge=0, le=6)
    quiet_enabled: bool | None = None
    quiet_from: time | None = None
    quiet_until: time | None = None
    timezone: str | None = None

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v:
            unknown = set(v) - set(CATEGORIES)
            if unknown:
                raise ValueError(f"unknown categories: {sorted(unknown)}")
        return v

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, v: str | None) -> str | None:
        if v is not None and v not in available_timezones():
            raise ValueError("Pick a timezone from the list.")
        return v
