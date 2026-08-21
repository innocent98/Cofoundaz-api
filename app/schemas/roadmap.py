from datetime import date

from pydantic import BaseModel, field_validator


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

    @field_validator("name", "order")
    @classmethod
    def _reject_explicit_null(cls, v: str | int | None) -> str | int:
        if v is None:
            raise ValueError("This field cannot be null.")
        return v
