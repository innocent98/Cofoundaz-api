import signal
import time
from collections.abc import Callable

from app.core.config import settings
from app.core.logger import log
from app.db.session import SessionLocal
from app.worker.runner import run_once


def register() -> None:
    """Import handler modules for their register_handler(...) side effects."""
    import app.worker.handlers.email  # noqa: F401


def main_loop(stop: Callable[[], bool]) -> None:
    while not stop():
        db = SessionLocal()
        try:
            run_once(db)
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad batch
            log.warning(f"[worker] run_once errored: {exc}")
            db.rollback()
        finally:
            db.close()
        time.sleep(settings.WORKER_POLL_INTERVAL)


def main() -> None:
    register()
    stopping = {"v": False}

    def _handle_sigterm(_signum, _frame):
        log.info("[worker] SIGTERM received; finishing and exiting")
        stopping["v"] = True

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    log.info("[worker] starting main loop")
    main_loop(stop=lambda: stopping["v"])


if __name__ == "__main__":
    main()
