from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

# A day of playback; a hard ceiling so a bogus client cannot store an absurd number.
MAX_POSITION_S = 86_400
MAX_NOTE_CHARS = 20_000


class EnrollmentCreate(BaseModel):
    course_id: str


class LessonProgressUpdate(BaseModel):
    """Complete a lesson (``completed: true``; un-completing is out of scope, spec section 1)
    and/or save where the learner paused (``position_s``). At least one is required."""

    completed: Literal[True] | None = None
    position_s: int | None = Field(default=None, ge=0, le=MAX_POSITION_S)

    @model_validator(mode="after")
    def _something_to_do(self) -> Self:
        if self.completed is None and self.position_s is None:
            raise ValueError("send completed: true, position_s, or both")
        return self


class LessonNoteUpdate(BaseModel):
    body: str = Field(max_length=MAX_NOTE_CHARS)
