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
