from pydantic import BaseModel, Field

from app.db.models.enums import SuggestionOp


class CanvasSave(BaseModel):
    blocks: dict = Field(default_factory=dict)
    version: int


class RecordCreate(BaseModel):
    data: dict = Field(default_factory=dict)


class SuggestionCreate(BaseModel):
    op: SuggestionOp
    target: dict = Field(default_factory=dict)
    payload: dict | None = None
    note: str | None = None


class PositioningMapSave(BaseModel):
    axes: dict = Field(default_factory=dict)
