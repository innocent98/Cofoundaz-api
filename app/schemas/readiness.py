import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ReadinessInputsIn(BaseModel):
    """Market size in minor units of `currency`. A field left out is left as it was."""

    model_config = ConfigDict(extra="forbid")

    tam_minor: int | None = Field(None, ge=0, le=10**15)
    sam_minor: int | None = Field(None, ge=0, le=10**15)
    som_minor: int | None = Field(None, ge=0, le=10**15)
    currency: Literal["NGN", "USD"] | None = None
    source_url: HttpUrl | None = None

    @model_validator(mode="after")
    def _nested(self) -> "ReadinessInputsIn":
        tam, sam, som = self.tam_minor, self.sam_minor, self.som_minor
        if tam is not None and sam is not None and sam > tam:
            raise ValueError("SAM cannot be larger than TAM.")
        if sam is not None and som is not None and som > sam:
            raise ValueError("SOM cannot be larger than SAM.")
        return self


class DeckReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: uuid.UUID


class QaStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus: Literal["general", "financials", "market", "team"]
    intensity: Literal["friendly_angel", "skeptical_vc", "term_sheet_grilling"]


class QaMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=2000)
    # How many answers the client believes are already recorded (the session's `turns`). A stale
    # value means this is a retry of an answer that was already received: 409 TURN_MISMATCH.
    expected_turn: int = Field(..., ge=0, le=10)


class NarrativeIn(BaseModel):
    """The whole draft (autosave sends the full text each time). Empty clears it."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., max_length=20_000)
