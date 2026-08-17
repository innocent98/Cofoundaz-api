from typing import Any

from pydantic import BaseModel


class AnswerRequest(BaseModel):
    question_key: str
    value: Any
