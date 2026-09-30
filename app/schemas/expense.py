import datetime as dt
import uuid

from pydantic import BaseModel, Field, model_validator

INT32 = 2_147_483_647


# `dt.` alias: see app/schemas/finance.py (Py3.14 deferred-annotation shadowing).
class ExpenseCreate(BaseModel):
    vendor: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=120)
    expense_date: dt.date
    amount_minor: int = Field(ge=0, le=INT32)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    recurring: bool = False
    notes: str | None = None


class ExpenseUpdate(BaseModel):
    vendor: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=120)
    expense_date: dt.date | None = None
    amount_minor: int | None = Field(default=None, ge=0, le=INT32)
    currency: str | None = Field(default=None, min_length=1, max_length=3)
    recurring: bool | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _no_explicit_null_on_required(self) -> "ExpenseUpdate":
        # Only `notes` is nullable on the row; an explicit null for any other field would hit a
        # NOT NULL violation at flush (500). Reject it here as a 422 instead. Omitting a field is
        # still fine (partial update) — this only fires when the field was explicitly sent as null.
        for name in ("vendor", "category", "expense_date", "amount_minor", "currency", "recurring"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class ExpenseResponse(BaseModel):
    id: uuid.UUID
    vendor: str
    category: str
    expense_date: dt.date
    amount_minor: int
    currency: str
    recurring: bool
    notes: str | None
    has_receipt: bool
    receipt_url: str | None
    transaction_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CategorySummaryRow(BaseModel):
    category: str
    total_minor: int
    percent: float


class CategorySummary(BaseModel):
    month: str
    currency: str
    total_minor: int
    rows: list[CategorySummaryRow]
