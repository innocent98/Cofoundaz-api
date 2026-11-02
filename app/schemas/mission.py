import uuid
from datetime import time

from pydantic import BaseModel, Field

from app.db.models.enums import TaskEffort


class MissionSettingsUpdate(BaseModel):
    mission_size: int | None = None
    delivery_time: time | None = None
    weekend_missions: bool | None = None


class MissionTaskCreate(BaseModel):
    title: str
    effort: TaskEffort | None = None


class MissionTaskAction(BaseModel):
    action: str
    order: int | None = None
    reject_reason: str | None = None


class UpcomingOrder(BaseModel):
    # The founder's order for the upcoming queue (PRD 04.2). Bounded: it's one roadmap's open tasks.
    roadmap_task_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
