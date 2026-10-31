"""Maintenance mode: refuse writes platform-wide with one check, not one per router (P6).

While `platform_settings.maintenance.enabled` is on, every request that is not a safe method
(GET, HEAD, OPTIONS) gets `503 MAINTENANCE_MODE`, unless its path is on the allow-list below.
Reads keep working, so founders can still see their data.

The allow-list is what keeps the platform recoverable and keeps money from being lost:
- `/auth`: anyone can still sign in, and staff can sign back in after a global sign-out.
- `/admin` and `/super-admin`: the staff consoles, so maintenance can be switched off.
- `/health`: liveness and readiness probes.
- `/webhooks`: Paystack, payment-provider and Mono events. A 503 here would make the provider
  retry for hours or give up, so these are processed as normal.

Background jobs (the worker, scheduled runs) don't pass through HTTP and are not paused.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.envelope import error_response
from app.core.logger import log
from app.db.session import SessionLocal, get_db
from app.platform.state import maintenance_state

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRY_AFTER_SECONDS = "60"

_API = settings.API_V1_STR
ALLOWED_PREFIXES = (
    f"{_API}/auth",
    f"{_API}/admin",
    f"{_API}/super-admin",
    f"{_API}/health",
    f"{_API}/webhooks",
    "/health",
)


def is_allowed_during_maintenance(path: str) -> bool:
    if ".." in path:  # never let a prefix match be walked out of
        return False
    return any(path == p or path.startswith(p + "/") for p in ALLOWED_PREFIXES)


def _state(request: Request) -> dict[str, object]:
    # Honour a test's `get_db` override, so a test's uncommitted switch is visible here.
    factory = request.app.dependency_overrides.get(get_db)
    if factory is not None:
        return maintenance_state(factory())
    with SessionLocal() as db:
        return maintenance_state(db)


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in SAFE_METHODS or is_allowed_during_maintenance(request.url.path):
            return await call_next(request)
        try:
            state = _state(request)
        except Exception:  # noqa: BLE001 - a failed lookup must not take the platform down
            log.exception("maintenance check failed; letting the request through")
            return await call_next(request)
        if not state["enabled"]:
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content=error_response("MAINTENANCE_MODE", str(state["message"])),
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )
