from pydantic import BaseModel, Field


class CanvasSave(BaseModel):
    blocks: dict = Field(default_factory=dict)
    version: int
