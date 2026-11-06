"""Funding Hub cap table request bodies (slice C). Money is integer minor units; shares are whole
numbers; rates are basis points (1% = 100)."""

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_MINOR = 1_000_000_000_000_000
MAX_SHARES = 1_000_000_000_000

HOLDER_KINDS = ("founder", "employee", "advisor", "investor", "pool")
SHARE_SECURITIES = ("common", "preferred", "option")
CONVERTIBLES = ("safe", "note")
SECURITIES = (*SHARE_SECURITIES, *CONVERTIBLES)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HolderCreate(_Strict):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(pattern=f"^({'|'.join(HOLDER_KINDS)})$")
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=5000)


class HolderUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _name_cannot_be_cleared(self) -> "HolderUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("A holder needs a name.")
        return self


class EntryFields(_Strict):
    """Everything an entry can carry. `EntryCreate` adds the cross-field rules."""

    security: str = Field(pattern=f"^({'|'.join(SECURITIES)})$")
    shares: int | None = Field(default=None, ge=1, le=MAX_SHARES)
    price_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    amount_minor: int | None = Field(default=None, ge=1, le=MAX_MINOR)
    valuation_cap_minor: int | None = Field(default=None, ge=1, le=MAX_MINOR)
    discount_bps: int | None = Field(default=None, ge=1, le=9999)
    safe_kind: str | None = Field(default=None, pattern="^(post_money|pre_money)$")
    mfn: bool = False
    interest_bps: int | None = Field(default=None, ge=0, le=10_000)
    issued_on: dt.date | None = None
    vesting_cliff_months: int | None = Field(default=None, ge=0, le=240)
    vesting_months: int | None = Field(default=None, ge=1, le=240)
    vesting_start_on: dt.date | None = None
    notes: str | None = Field(default=None, max_length=5000)


class EntryCreate(EntryFields):
    holder_id: uuid.UUID

    @model_validator(mode="after")
    def _shape(self) -> "EntryCreate":
        if self.security in SHARE_SECURITIES:
            if self.shares is None:
                raise ValueError("Shares are required for stock and options.")
            bad = [
                n
                for n in (
                    "amount_minor",
                    "valuation_cap_minor",
                    "discount_bps",
                    "safe_kind",
                    "interest_bps",
                )
                if getattr(self, n) is not None
            ]
            if bad or self.mfn:
                raise ValueError("Amount, cap, discount and MFN belong on SAFEs and notes.")
        else:
            if self.amount_minor is None:
                raise ValueError("An amount is required for a SAFE or note.")
            if self.shares is not None or self.price_minor is not None:
                raise ValueError(
                    "A SAFE or note has no shares or share price until it converts in a round."
                )
            if self.vesting_months is not None or self.vesting_start_on is not None:
                raise ValueError("Vesting applies to stock and options only.")
            if self.interest_bps is not None and self.security != "note":
                raise ValueError("Interest applies to notes only.")
            if self.interest_bps and self.issued_on is None:
                raise ValueError("A note with interest needs the date it was issued.")
        if (self.vesting_months is None) != (self.vesting_start_on is None):
            raise ValueError("Give both the vesting term and the start date, or neither.")
        if self.vesting_months is not None:
            cliff = self.vesting_cliff_months or 0
            if cliff > self.vesting_months:
                raise ValueError("The cliff cannot be longer than the vesting term.")
        elif self.vesting_cliff_months is not None:
            raise ValueError("A cliff needs a vesting term and start date.")
        return self


class EntryUpdate(_Strict):
    """Any field of an entry; the merged result is validated as a whole. `holder_id` moves it."""

    holder_id: uuid.UUID | None = None
    security: str | None = Field(default=None, pattern=f"^({'|'.join(SECURITIES)})$")
    shares: int | None = Field(default=None, ge=1, le=MAX_SHARES)
    price_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    amount_minor: int | None = Field(default=None, ge=1, le=MAX_MINOR)
    valuation_cap_minor: int | None = Field(default=None, ge=1, le=MAX_MINOR)
    discount_bps: int | None = Field(default=None, ge=1, le=9999)
    safe_kind: str | None = Field(default=None, pattern="^(post_money|pre_money)$")
    mfn: bool | None = None
    interest_bps: int | None = Field(default=None, ge=0, le=10_000)
    issued_on: dt.date | None = None
    vesting_cliff_months: int | None = Field(default=None, ge=0, le=240)
    vesting_months: int | None = Field(default=None, ge=1, le=240)
    vesting_start_on: dt.date | None = None
    notes: str | None = Field(default=None, max_length=5000)


class RoundSpec(_Strict):
    name: str = Field(default="Round", min_length=1, max_length=80)
    new_money_minor: int = Field(ge=1, le=MAX_MINOR)
    pre_money_minor: int = Field(ge=1, le=MAX_MINOR)
    pool_target_bps: int = Field(default=0, ge=0, le=9999)


class ModelRequest(_Strict):
    """Stateless model of one or more rounds on the current cap table."""

    rounds: list[RoundSpec] = Field(min_length=1, max_length=5)
    as_of: dt.date | None = None


class ScenarioCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    inputs: ModelRequest


class ScenarioUpdate(_Strict):
    name: str = Field(min_length=1, max_length=120)


def as_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
