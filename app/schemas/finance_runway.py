import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INT32_MAX = 2_147_483_647

_ASSUMPTION_FIELDS = ("mom_growth_percent", "hiring_spend_minor", "one_off_costs_minor")


class AssumptionsUpdate(BaseModel):
    """Runway keeps three scalars of its own. Hiring and one-off rows live in the Financial Model
    (D-FIN-12) and the projection reads them from there, so sending them here is an error, as is
    any other unknown field: a body that was silently half-applied is worse than a refusal."""

    model_config = ConfigDict(extra="forbid")

    mom_growth_percent: int | None = Field(default=None, ge=0, le=1000)
    hiring_spend_minor: int | None = Field(default=None, ge=0, le=INT32_MAX)
    one_off_costs_minor: int | None = Field(default=None, ge=0, le=INT32_MAX)

    # Declared only so the refusal names the field (field_errors[].field) instead of a bare
    # "extra input"; a value for any of them is always rejected.
    hiring_plan: Any = Field(default=None, description="Rejected: edit it in the Financial Model.")
    one_offs: Any = Field(default=None, description="Rejected: edit it in the Financial Model.")
    capex: Any = Field(default=None, description="Rejected: edit it in the Financial Model.")

    @field_validator("hiring_plan", "one_offs", "capex", mode="before")
    @classmethod
    def _model_rows_live_elsewhere(cls, _v: Any) -> Any:
        raise ValueError(
            "The runway reads hiring and one-off rows from the Financial Model; "
            "edit them there (PATCH /finance/model/{model_id}/assumptions)."
        )

    @model_validator(mode="after")
    def _no_explicit_null(self) -> "AssumptionsUpdate":
        # All three columns are NOT NULL; an explicit null would hit a constraint violation at
        # flush (500). Reject it as a 422 instead. Omitting a field is still a valid partial update.
        for name in _ASSUMPTION_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class ScenarioPoint(BaseModel):
    month: str  # "YYYY-MM"
    cash_balance: int
    net: int


class Scenario(BaseModel):
    runway_months: float | None
    cash_out_date: str | None  # "YYYY-MM"
    avg_net_burn_minor: int
    by_month: list[ScenarioPoint]


class AssumptionsOut(BaseModel):
    mom_growth_percent: int
    hiring_spend_minor: int
    one_off_costs_minor: int


class RunwayBaseline(BaseModel):
    cash_on_hand: int
    monthly_burn: int
    monthly_revenue: int
    currency: str


class ModelHire(BaseModel):
    role: str
    start_month: str  # "YYYY-MM"
    monthly_cost_minor: int


class ModelOneOff(BaseModel):
    label: str
    month: str  # "YYYY-MM"
    amount_minor: int


class ModelRows(BaseModel):
    """The Financial Model rows the projection is reading right now (read-only here).

    `model_id` is null when the workspace has no completed model yet; the lists are then empty
    and the projection uses only the three scalar assumptions."""

    model_id: uuid.UUID | None
    href: str
    hiring_plan: list[ModelHire]
    one_offs: list[ModelOneOff]


class RunwayResponse(BaseModel):
    assumptions: AssumptionsOut
    model_rows: ModelRows
    baseline: RunwayBaseline
    horizon_months: int
    scenarios: dict[str, Scenario]
