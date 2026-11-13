"""Funding Hub grant request bodies (slice E). Money is integer minor units."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_MINOR = 1_000_000_000_000_000
Status = Literal["planning", "drafting", "submitted", "awarded", "rejected", "withdrawn"]
Currency = Literal["NGN", "USD", "EUR", "GBP", "CAD"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApplicationCreate(_Strict):
    """A grant the founder found themselves (not in the catalogue)."""

    name: str = Field(min_length=1, max_length=200)
    funder: str = Field(default="", max_length=200)
    deadline: dt.date | None = None
    amount_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    currency: Currency | None = None
    notes: str | None = Field(default=None, max_length=3000)


class ApplicationUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    funder: str | None = Field(default=None, max_length=200)
    status: Status | None = None
    deadline: dt.date | None = None
    amount_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    currency: Currency | None = None
    notes: str | None = Field(default=None, max_length=3000)


class AnswerCreate(_Strict):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(default="", max_length=8000)


class AnswerUpdate(_Strict):
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    answer: str | None = Field(default=None, max_length=8000)


class DraftRequest(_Strict):
    overwrite: bool = False
