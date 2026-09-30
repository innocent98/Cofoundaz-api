import datetime as dt
import uuid

from pydantic import BaseModel, Field, model_validator

INT64 = 9_223_372_036_854_775_807
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


# `dt.` alias: see app/schemas/finance.py (Py3.14 deferred-annotation shadowing).
class BudgetCreate(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    period_month: str = Field(pattern=MONTH_PATTERN)
    limit_minor: int = Field(ge=0, le=INT64)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    notes: str | None = None


class BudgetUpdate(BaseModel):
    limit_minor: int | None = Field(default=None, ge=0, le=INT64)
    notes: str | None = None

    @model_validator(mode="after")
    def _no_explicit_null_on_limit(self) -> "BudgetUpdate":
        # `limit_minor` is NOT NULL on the row; an explicit null would 500 at flush. Omitting it
        # is fine (partial update); `notes` is the only nullable field.
        if "limit_minor" in self.model_fields_set and self.limit_minor is None:
            raise ValueError("limit_minor may not be null")
        return self


class SeedRequest(BaseModel):
    period_month: str = Field(pattern=MONTH_PATTERN)


class BudgetResponse(BaseModel):
    id: uuid.UUID
    category: str
    period_month: str
    limit_minor: int
    currency: str
    notes: str | None
    spent_minor: int
    variance_minor: int
    over_budget: bool
    percent_used: float | None
    created_at: dt.datetime
    updated_at: dt.datetime
