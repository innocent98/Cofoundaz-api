from datetime import time

from pydantic import BaseModel


class MissionSettingsUpdate(BaseModel):
    mission_size: int | None = None
    delivery_time: time | None = None
    weekend_missions: bool | None = None
