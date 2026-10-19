from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.enums import SuggestionOp


class CanvasSave(BaseModel):
    blocks: dict = Field(default_factory=dict)
    version: int


class CanvasRestore(BaseModel):
    version: int  # the canvas's CURRENT version (optimistic concurrency, as on PUT)


class RecordCreate(BaseModel):
    data: dict = Field(default_factory=dict)


class SuggestionCreate(BaseModel):
    op: SuggestionOp
    target: dict = Field(default_factory=dict)
    payload: dict | None = None
    note: str | None = None


class PositioningMapSave(BaseModel):
    axes: dict = Field(default_factory=dict)


class CanvasAiFill(BaseModel):
    # Per-section assist (PRD 08): one block key; omitted = whole-canvas fill as before.
    block: str | None = Field(default=None, min_length=1, max_length=60)


class MissionVisionOptions(BaseModel):
    field: Literal["mission", "vision"]


class CompetitorPrice(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: str = Field(min_length=1, max_length=120)


class PricingAdvisorRequest(BaseModel):
    competitor_prices: list[CompetitorPrice] = Field(default_factory=list, max_length=20)
    willingness_to_pay: str | None = Field(default=None, max_length=2000)
