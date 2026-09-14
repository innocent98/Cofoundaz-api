from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.logger import log

Handler = Callable[[Session, dict], None]


class EventBus(Protocol):
    def publish(self, db: Session, event: str, payload: dict) -> None: ...
    def subscribe(self, event: str, handler: Handler) -> None: ...


class DispatchingEventBus:
    """Synchronous, same-transaction event bus.

    `publish` records + logs the event (as before) and invokes every handler
    subscribed to it, each inside its own SAVEPOINT + try/except: a handler that
    raises rolls back only its own writes and is logged — it can never break the
    action that published the event. Successful handlers' writes are part of the
    caller's transaction and commit with it.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def publish(self, db: Session, event: str, payload: dict) -> None:
        self.published.append((event, payload))
        log.info(f"[event] {event} {payload}")
        for handler in self._handlers.get(event, []):
            try:
                with db.begin_nested():
                    handler(db, payload)
            except Exception as exc:  # noqa: BLE001 - a broken handler must not break the action
                log.warning(f"[event] handler for {event!r} failed: {exc}")


event_bus: EventBus = DispatchingEventBus()
