import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import CoachDifficulty


class CoachSessionCreate(BaseModel):
    """Setup for a role-play: a deal (context built from it) or free scenario text."""

    deal_id: uuid.UUID | None = None
    scenario: str | None = Field(default=None, max_length=2000)
    persona: str = Field(min_length=1, max_length=200)
    difficulty: CoachDifficulty = CoachDifficulty.neutral
    # Optional opening line from the seller; same shape as the co-founder chat's opener.
    message: str | None = Field(default=None, max_length=4000)

    @field_validator("scenario", "message", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("persona")
    @classmethod
    def _strip_persona(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Persona is required.")
        return v


class CoachMessagePost(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
