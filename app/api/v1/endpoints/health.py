import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logger import log
from app.core.redis import get_redis
from app.db.session import get_db

router = APIRouter()


@router.get("")
def health_check() -> dict[str, str]:
    """Liveness. Deliberately touches no dependency.

    Answers only "is this process up and serving?". Used by the Docker
    HEALTHCHECK and by nginx, both of which should NOT pull the API out of
    rotation just because Postgres blipped - that would turn a recoverable
    database hiccup into a full outage.
    """
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


def _check_database(db: Session) -> tuple[bool, str | None]:
    try:
        db.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        # Log the real exception server-side; return only a generic reason to the
        # caller. Driver errors embed the full DSN (host, user, and sometimes the
        # password) and this endpoint is unauthenticated.
        log.error(f"readiness: database check failed: {exc!r}")
        return False, "unavailable"


def _check_redis() -> tuple[bool, str | None]:
    try:
        get_redis().ping()
        return True, None
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        log.error(f"readiness: redis check failed: {exc!r}")
        return False, "unavailable"


@router.get("/ready")
def readiness_check(
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Readiness. Verifies the process can actually serve traffic.

    Checks every backing service the app cannot work without, and returns 503
    when any of them is down. This is the gate the CD pipeline waits on after a
    deploy: a container that is *running* but cannot reach Postgres has not
    deployed successfully, and the old image should stay in service.

    Unauthenticated by design (a deploy gate and an uptime monitor both need it
    before any credential exists), so it is kept deliberately cheap - one
    `SELECT 1` and one Redis PING, no table scans - and it never echoes
    connection strings or driver messages back to the caller.
    """
    db_ok, db_reason = _check_database(db)
    redis_ok, redis_reason = _check_redis()

    checks: dict[str, Any] = {
        "database": {"status": "ok" if db_ok else "error", "reason": db_reason},
        "redis": {"status": "ok" if redis_ok else "error", "reason": redis_reason},
    }

    ready = db_ok and redis_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready else "not_ready",
        "timestamp": datetime.now(UTC).isoformat(),
        # Baked into the image by the Dockerfile from the CI build args, so an
        # operator can confirm which commit is actually serving without shelling
        # into the box. "unknown" for a local/dev build.
        "revision": os.getenv("APP_GIT_SHA", "unknown"),
        "checks": checks,
    }
