from datetime import time

from pydantic import BaseModel

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
