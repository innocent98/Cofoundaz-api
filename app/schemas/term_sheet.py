"""Funding Hub term sheet request bodies (slice D). Money is integer minor units in the round
currency; rates are basis points (1% = 100); multiples are hundredths (1.5x = 150)."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_MINOR = 1_000_000_000_000_000

Stage = Literal["pre_seed", "seed", "series_a", "series_b", "later", "other"]
ProRata = Literal["none", "standard", "super"]
AntiDilution = Literal["none", "broad_based", "narrow_based", "full_ratchet"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Terms(_Strict):
    """The terms we compare and flag. Every field is optional: a sheet may not state them all, and
    an unstated term is never flagged. `null` and absent both mean "not stated"."""

    stage: Stage | None = None
    pre_money_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    investment_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR)
    option_pool_pre_bps: int | None = Field(
        default=None, ge=0, le=10_000
    )  # of pre-money, fully diluted
    liq_pref_x100: int | None = Field(default=None, ge=0, le=2000)  # 100 = 1.0x
    participating: bool | None = None
    participation_cap_x100: int | None = Field(default=None, ge=100, le=5000)
    investor_board_seats: int | None = Field(default=None, ge=0, le=20)
    founder_board_seats: int | None = Field(default=None, ge=0, le=20)
    pro_rata: ProRata | None = None
    exclusivity_days: int | None = Field(default=None, ge=0, le=730)
    anti_dilution: AntiDilution | None = None
    notes: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def _cap_needs_participation(self) -> "Terms":
        if self.participation_cap_x100 is not None and self.participating is False:
            raise ValueError("A participation cap only applies to participating preferred.")
        return self


class TermSheetCreate(_Strict):
    """A sheet typed in by hand: confirmed on save."""

    label: str = Field(min_length=1, max_length=160)
    investor_id: uuid.UUID | None = None
    terms: Terms


class TermSheetExtract(_Strict):
    """Queue extraction from a PDF already in Documents."""

    file_id: uuid.UUID
    investor_id: uuid.UUID | None = None
    label: str | None = Field(default=None, min_length=1, max_length=160)


class TermSheetConfirm(_Strict):
    """The final values after the founder has reviewed (and maybe corrected) the proposal."""

    terms: Terms
    label: str | None = Field(default=None, min_length=1, max_length=160)


class TermSheetUpdate(_Strict):
    """Edit a confirmed sheet. Leaving `terms` out leaves them; sending it replaces them whole."""

    label: str | None = Field(default=None, min_length=1, max_length=160)
    investor_id: uuid.UUID | None = None
    terms: Terms | None = None

    @model_validator(mode="after")
    def _nulls(self) -> "TermSheetUpdate":
        if "label" in self.model_fields_set and self.label is None:
            raise ValueError("A term sheet needs a label.")
        if "terms" in self.model_fields_set and self.terms is None:
            raise ValueError("Send the terms, or leave the field out.")
        return self


def today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()
