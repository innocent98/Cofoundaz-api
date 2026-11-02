import datetime as dt
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.finance_rules import ngn_only, tidy_category

INT64 = 9_223_372_036_854_775_807
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


# `dt.` alias: see app/schemas/finance.py (Py3.14 deferred-annotation shadowing).
class BudgetCreate(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    period_month: str = Field(pattern=MONTH_PATTERN)
    limit_minor: int = Field(ge=0, le=INT64)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return str(ngn_only(v))

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        cleaned = tidy_category(v)
        if cleaned is None:
            raise ValueError("category may not be blank")
        return cleaned


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
    # An unbudgeted row (spend in a category with no budget) has id/limit_minor/variance_*/
    # created_at/updated_at null and `unbudgeted: true`.
    id: uuid.UUID | None
    category: str
    period_month: str
    limit_minor: int | None
    currency: str
    notes: str | None
    spent_minor: int
    variance_minor: int | None
    variance_percent: float | None
    over_budget: bool
    percent_used: float | None
    status: str  # "under" | "near" | "over" | "unbudgeted" (worded, never colour alone)
    unbudgeted: bool
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
