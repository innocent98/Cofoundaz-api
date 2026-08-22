import uuid
from datetime import date

from pydantic import BaseModel, field_validator

from app.db.models.enums import RoadmapStatus, TaskEffort


class PhaseCreate(BaseModel):
    name: str
    order: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None


class PhaseUpdate(BaseModel):
    name: str | None = None
    order: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None

    @field_validator("name", "order")
    @classmethod
    def _reject_explicit_null(cls, v: str | int | None) -> str | int:
        if v is None:
            raise ValueError("This field cannot be null.")
        return v


class MilestoneCreate(BaseModel):
    phase_id: uuid.UUID
    title: str
    description: str | None = None
    due_on: date | None = None
    owner_id: uuid.UUID | None = None
    status: RoadmapStatus | None = None
    order: int | None = None


class MilestoneUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_on: date | None = None
    owner_id: uuid.UUID | None = None
    status: RoadmapStatus | None = None
    order: int | None = None

    @field_validator("title", "status", "order")
    @classmethod
    def _reject_explicit_null(
        cls, v: str | int | RoadmapStatus | None
    ) -> str | int | RoadmapStatus:
        if v is None:
            raise ValueError("This field cannot be null.")
        return v


class TaskCreate(BaseModel):
    milestone_id: uuid.UUID
    title: str
    description: str | None = None
    effort: TaskEffort | None = None
    status: RoadmapStatus | None = None
    assignee_id: uuid.UUID | None = None
    due_on: date | None = None
    order: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    effort: TaskEffort | None = None
    status: RoadmapStatus | None = None
    assignee_id: uuid.UUID | None = None
    due_on: date | None = None
    order: int | None = None

    @field_validator("title", "effort", "status", "order")
    @classmethod
    def _reject_explicit_null(
        cls, v: str | int | TaskEffort | RoadmapStatus | None
    ) -> str | int | TaskEffort | RoadmapStatus:
        if v is None:
            raise ValueError("This field cannot be null.")
        return v


class DependencyCreate(BaseModel):
    depends_on_task_id: uuid.UUID
