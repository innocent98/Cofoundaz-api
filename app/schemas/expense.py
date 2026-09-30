import datetime as dt
import uuid

from pydantic import BaseModel, Field

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
