from typing import Protocol

from app.core.logger import log


class EventBus(Protocol):
    def publish(self, event: str, payload: dict) -> None: ...


class LogEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event: str, payload: dict) -> None:
        self.published.append((event, payload))
        log.info(f"[event] {event} {payload}")


event_bus: EventBus = LogEventBus()
