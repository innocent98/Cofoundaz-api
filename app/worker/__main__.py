import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import FrameType

from app.core.config import settings
from app.core.logger import log
from app.db.session import SessionLocal
from app.worker.runner import run_once
from app.worker.scheduler import scheduler_tick


def register() -> None:
    """Import handler modules for their register_handler(...) side effects."""
    import app.worker.handlers.email  # noqa: F401
    import app.worker.handlers.scheduled  # noqa: F401


def main_loop(stop: Callable[[], bool]) -> None:
    last_tick = 0.0
    while not stop():
        db = SessionLocal()
        try:
            run_once(db)
            now_ts = time.monotonic()
            if now_ts - last_tick >= settings.SCHEDULER_INTERVAL:
                scheduler_tick(db, now=datetime.now(UTC))
                last_tick = now_ts
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad batch
            log.warning(f"[worker] loop iteration errored: {exc}")
            db.rollback()
        finally:
            db.close()
        time.sleep(settings.WORKER_POLL_INTERVAL)


def main() -> None:
    register()
    stopping = {"v": False}

    def _handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
        log.info("[worker] SIGTERM received; finishing and exiting")
        stopping["v"] = True

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    log.info("[worker] starting main loop")
    main_loop(stop=lambda: stopping["v"])


if __name__ == "__main__":
    main()
