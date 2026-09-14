from typing import Literal

from pydantic import BaseModel


class EnrollmentCreate(BaseModel):
    course_id: str


class LessonProgressUpdate(BaseModel):
    """v1 supports completing a lesson only; un-completing is out of scope (spec section 1)."""

    completed: Literal[True]
