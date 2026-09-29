from pydantic import BaseModel, Field, model_validator

INT32_MAX = 2_147_483_647

_ASSUMPTION_FIELDS = ("mom_growth_percent", "hiring_spend_minor", "one_off_costs_minor")


class AssumptionsUpdate(BaseModel):
    mom_growth_percent: int | None = Field(default=None, ge=0, le=1000)
    hiring_spend_minor: int | None = Field(default=None, ge=0, le=INT32_MAX)
    one_off_costs_minor: int | None = Field(default=None, ge=0, le=INT32_MAX)

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


class RunwayResponse(BaseModel):
    assumptions: AssumptionsOut
    baseline: RunwayBaseline
    horizon_months: int
    scenarios: dict[str, Scenario]
