from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)


class AssumptionCreate(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    risk: RiskLevel
    status: AssumptionStatus | None = None


class AssumptionUpdate(BaseModel):
    statement: str | None = Field(default=None, min_length=1, max_length=2000)
    risk: RiskLevel | None = None
    status: AssumptionStatus | None = None


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: ExperimentType
    config: dict[str, Any] = Field(default_factory=dict)
    status: ExperimentStatus | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    assumption_ids: list[str] = Field(default_factory=list)


class ExperimentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: ExperimentType | None = None
    config: dict[str, Any] | None = None
    status: ExperimentStatus | None = None
    metrics: dict[str, Any] | None = None
    assumption_ids: list[str] | None = None


class InterviewCreate(BaseModel):
    interviewee: str = Field(min_length=1, max_length=255)
    held_on: date
    verdict: InterviewVerdict
    segment: str | None = Field(default=None, max_length=120)
    notes: str = ""
    key_quotes: list[Any] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)


class InterviewUpdate(BaseModel):
    interviewee: str | None = Field(default=None, min_length=1, max_length=255)
    held_on: date | None = None
    verdict: InterviewVerdict | None = None
    segment: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    key_quotes: list[Any] | None = None
    assumption_ids: list[str] | None = None


class SurveyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    questions: list[Any] = Field(default_factory=list)


class SurveyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    questions: list[Any] | None = None
    status: SurveyStatus | None = None


class ResponseSubmit(BaseModel):
    """The only body a member of the public ever sends."""

    answers: dict[str, Any] = Field(default_factory=dict)


class ScriptsGenerate(BaseModel):
    assumption_ids: list[str] = Field(default_factory=list)
