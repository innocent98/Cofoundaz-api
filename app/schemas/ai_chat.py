from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    # Optional opening message; when present the conversation starts with the founder's
    # turn + a generating assistant turn, same as POST .../messages.
    message: str | None = Field(default=None, max_length=8000)


class PostMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
