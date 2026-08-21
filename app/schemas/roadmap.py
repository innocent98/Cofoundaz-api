from datetime import date

from pydantic import BaseModel


class PhaseCreate(BaseModel):
    name: str
    order: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None


class PhaseUpdate(BaseModel):
    name: str | None = None
    order: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None
