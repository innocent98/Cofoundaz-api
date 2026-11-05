"""Funding Hub request bodies (slice A). Money is integer minor units in the round's currency."""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.db.models.enums import (
    FundingCurrency,
    FundingRoundStatus,
    InvestorEventKind,
    InvestorStage,
    InvestorType,
    PassedReason,
)

# 10^15 minor units is far past any real raise and keeps sums inside BIGINT.
MAX_MINOR = 1_000_000_000_000_000


def _minor() -> int | None:
    return Field(default=None, ge=0, le=MAX_MINOR)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoundUpdate(_Strict):
    """PUT /round. Fields left out are untouched; an explicit null clears target and pre-money."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: FundingRoundStatus | None = None
    currency: FundingCurrency | None = None
    target_minor: int | None = _minor()
    pre_money_minor: int | None = _minor()


class InvestorCreate(_Strict):
    name: str = Field(min_length=1, max_length=160)
    firm: str | None = Field(default=None, max_length=160)
    type: InvestorType = InvestorType.angel
    stage: InvestorStage = InvestorStage.research
    warm: bool = False
    check_min_minor: int | None = _minor()
    check_max_minor: int | None = _minor()
    committed_minor: int | None = _minor()
    next_step: str | None = Field(default=None, max_length=300)
    next_step_on: dt.date | None = None
    thesis_notes: str | None = Field(default=None, max_length=5000)

    @field_validator("stage")
    @classmethod
    def _not_passed(cls, v: InvestorStage) -> InvestorStage:
        if v == InvestorStage.passed:
            raise PydanticCustomError(
                "stage_passed", "Add the investor first, then move them to Passed with a reason."
            )
        return v

    @field_validator("check_max_minor")
    @classmethod
    def _range(cls, v: int | None, info: ValidationInfo) -> int | None:
        lo = info.data.get("check_min_minor")
        if v is not None and lo is not None and v < lo:
            raise PydanticCustomError(
                "cheque_range", "The maximum cheque can't be below the minimum."
            )
        return v


class InvestorUpdate(_Strict):
    """PATCH. The stage moves through POST /stage so history and Passed reasons stay honest."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    firm: str | None = Field(default=None, max_length=160)
    type: InvestorType | None = None
    warm: bool | None = None
    check_min_minor: int | None = _minor()
    check_max_minor: int | None = _minor()
    committed_minor: int | None = _minor()
    next_step: str | None = Field(default=None, max_length=300)
    next_step_on: dt.date | None = None
    thesis_notes: str | None = Field(default=None, max_length=5000)


class StageMove(_Strict):
    stage: InvestorStage
    passed_reason: PassedReason | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)
    note: str | None = Field(default=None, max_length=2000)


class EventCreate(_Strict):
    kind: InvestorEventKind
    body: str = Field(min_length=1, max_length=4000)
    occurred_at: dt.datetime | None = None


class RecordFunding(_Strict):
    """Confirm the money arrived. `amount_minor` is in NGN (the Finance ledger is NGN only); it is
    optional only when the round itself is in NGN."""

    amount_minor: int | None = Field(default=None, gt=0, le=2_147_483_647)
    received_on: dt.date | None = None
