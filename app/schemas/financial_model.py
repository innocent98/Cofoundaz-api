import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel


class ModelStatementRow(BaseModel):
    label: str
    values: list[int]


class ModelStatement(BaseModel):
    rows: list[ModelStatementRow]


class FinancialModelResponse(BaseModel):
    id: uuid.UUID
    status: str
    horizon_months: int
    currency: str
    assumptions: dict[str, Any] | None = None
    pnl: ModelStatement | None = None
    cash_flow: ModelStatement | None = None
    balance_sheet: ModelStatement | None = None
    months: list[str] | None = None
    error: str | None = None
    generated_at: dt.datetime | None = None
    created_at: dt.datetime
