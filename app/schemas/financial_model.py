import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# A single input above this is a typo, not a plan (10 billion naira in kobo). Keeps every
# projected figure far inside what the JSON and the UI can carry.
MAX_MINOR = 10**12
MAX_ITEMS = 50

MonthStr = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ModelStatementRow(BaseModel):
    label: str
    values: list[int]


class ModelStatement(BaseModel):
    rows: list[ModelStatementRow]


class YearlyPeriod(BaseModel):
    label: str
    start_month: str
    end_month: str


class YearlyRollup(BaseModel):
    periods: list[YearlyPeriod]
    pnl: ModelStatement | None = None
    cash_flow: ModelStatement | None = None
    balance_sheet: ModelStatement | None = None


class BalanceCheck(BaseModel):
    balanced: bool
    max_abs_difference_minor: int


class FinancialModelResponse(BaseModel):
    id: uuid.UUID
    status: str
    horizon_months: int
    currency: str
    # What the engine used: the AI's baseline with the founder's edits laid on top.
    assumptions: dict[str, Any] | None = None
    ai_assumptions: dict[str, Any] | None = None
    overrides: dict[str, Any] | None = None
    # True when assumptions were edited after the statements were last calculated.
    stale: bool = False
    pnl: ModelStatement | None = None
    cash_flow: ModelStatement | None = None
    balance_sheet: ModelStatement | None = None
    months: list[str] | None = None
    yearly: YearlyRollup | None = None
    balance_check: BalanceCheck | None = None
    # Only on a Recalculate: edits that no longer fall inside the projection (never dropped
    # silently; they stay saved and apply again if the window moves back).
    warnings: list[str] = []
    error: str | None = None
    generated_at: dt.datetime | None = None
    recalculated_at: dt.datetime | None = None
    created_at: dt.datetime


class HiringItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Label
    start_month: MonthStr
    monthly_cost_minor: int = Field(gt=0, le=MAX_MINOR)


class OneOffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label
    month: MonthStr
    amount_minor: int = Field(gt=0, le=MAX_MINOR)


class CapexItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label
    month: MonthStr
    amount_minor: int = Field(gt=0, le=MAX_MINOR)


class ModelAssumptionsPatch(BaseModel):
    """Founder edits. A field that is omitted is untouched; `null` clears that edit and falls
    back to the AI's value; a list replaces the whole list."""

    model_config = ConfigDict(extra="forbid")

    monthly_revenue_growth_pct: float | None = Field(
        default=None, ge=-50, le=100, allow_inf_nan=False
    )
    monthly_opex_growth_pct: float | None = Field(default=None, ge=-50, le=100, allow_inf_nan=False)
    cogs_pct_of_revenue: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    ar_days: int | None = Field(default=None, ge=0, le=120)
    ap_days: int | None = Field(default=None, ge=0, le=120)
    revenue_0_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    monthly_costs_0_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    depreciation_months: int | None = Field(default=None, ge=6, le=120)
    hiring_plan: list[HiringItem] | None = Field(default=None, max_length=MAX_ITEMS)
    one_offs: list[OneOffItem] | None = Field(default=None, max_length=MAX_ITEMS)
    capex: list[CapexItem] | None = Field(default=None, max_length=MAX_ITEMS)


class RecalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Re-read cash, revenue, costs and funding from the ledger instead of reusing the figures
    # captured when the model was generated.
    refresh_actuals: bool = False
