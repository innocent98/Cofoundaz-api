"""Event-driven Health Score refresh (PRD Module 06: "recompute on relevant events, min
interval 15 min"). Subscribed in both the API and the worker process (jobs publish too)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.startup import Startup
from app.platform.events import event_bus

# Events that change a live signal's input (see live_signals.SIGNALS).
REFRESH_EVENTS: tuple[str, ...] = (
    "mission.task.completed",
    "mission.completed",
    "roadmap.milestone.completed",
    "roadmap.replanned",
    "roadmap.template.applied",
    "document.signature.completed",
    "workspace.member.joined",
    "business.plan.generated",
)

_registered_buses: set[int] = set()


def _refresh(db: Session, event: str, payload: dict) -> None:
    from app.services.health_score.service import refresh_health_score  # import cycle

    sid = payload.get("startup_id")
    if not sid:
        return
    startup = db.get(Startup, uuid.UUID(str(sid)))
    if startup is not None:
        refresh_health_score(db, startup, trigger=event[:40])


def register(bus: Any = event_bus) -> None:
    """Idempotent for the default bus (same guard as the notifications registry)."""
    if bus is event_bus and id(bus) in _registered_buses:
        return
    for event in REFRESH_EVENTS:
        bus.subscribe(event, lambda db, payload, e=event: _refresh(db, e, payload))
    if bus is event_bus:
        _registered_buses.add(id(bus))
