import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Surrounding whitespace is stripped first, so a blank message is a 422, never a model call.
ChatText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class CreateConversationRequest(BaseModel):
    # The opening message is required: an empty conversation is never created (D-AI-8).
    message: ChatText
    # "Ask {agent}": pin the conversation to one specialist (a key from GET /ai/agents).
    agent_key: str | None = Field(default=None, max_length=24)


class PostMessageRequest(BaseModel):
    content: ChatText


class ConversationIdsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["up", "down"] | None
    note: str | None = Field(default=None, max_length=500)


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=7, ge=1, le=30)
