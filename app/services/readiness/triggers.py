"""Event-driven Investor Readiness refresh (min interval 15 min, the same policy as Health Score).

Subscribes to the events that change a signal's input; inputs with no event (finance, validation,
sales) are picked up on read when stale and by the nightly pass."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.health_score.triggers import REFRESH_EVENTS as HEALTH_EVENTS

REFRESH_EVENTS: tuple[str, ...] = (
    *HEALTH_EVENTS,
    "business.canvas.updated",
    "sales.deal.won",
    "finance.model.generated",
    "finance.invoice.paid",
    "validation.assumption.validated",
    "validation.assumption.invalidated",
    "document.created",
    "readiness.deck.reviewed",
)

# The founder is waiting on these: refresh now instead of honouring the 15 minute throttle.
FORCE_EVENTS: frozenset[str] = frozenset({"readiness.deck.reviewed"})

_registered_buses: set[int] = set()


def _refresh(db: Session, event: str, payload: dict) -> None:
    from app.services.readiness.service import refresh_readiness  # import cycle

    sid = payload.get("startup_id")
    if not sid:
        return
    startup = db.get(Startup, uuid.UUID(str(sid)))
    if startup is not None:
        refresh_readiness(db, startup, trigger=event[:40], force=event in FORCE_EVENTS)


def register(bus: Any = event_bus) -> None:
    """Idempotent for the default bus."""
    if bus is event_bus and id(bus) in _registered_buses:
        return
    for event in REFRESH_EVENTS:
        bus.subscribe(event, lambda db, payload, e=event: _refresh(db, e, payload))
    if bus is event_bus:
        _registered_buses.add(id(bus))
