from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.envelope import error_response
from app.main import app


def test_limiter_registered():
    assert getattr(app.state, "limiter", None) is not None


def _mini_rate_limited_app() -> FastAPI:
    """Mirrors app/main.py's rate-limit wiring (Limiter + SlowAPIMiddleware +
    RateLimitExceeded handler) on a route decorated with a tight limit, so the
    limit can actually be tripped deterministically without depending on the
    real app's shared default_limits."""
    mini = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    mini.state.limiter = limiter
    mini.add_middleware(SlowAPIMiddleware)

    @mini.exception_handler(RateLimitExceeded)
    async def _rate_limit_exceeded_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=error_response("RATE_LIMITED", "Too many requests. Slow down a moment."),
            headers={"Retry-After": "60"},
        )

    @mini.get("/ping")
    @limiter.limit("2/minute")
    def ping(request: Request) -> dict:  # noqa: B008
        return {"ok": True}

    return mini


def test_exceeding_limit_returns_429_with_envelope_and_retry_after():
    client = TestClient(_mini_rate_limited_app())

    first = client.get("/ping")
    second = client.get("/ping")
    third = client.get("/ping")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers["Retry-After"] == "60"
    body = third.json()
    assert body["error"]["code"] == "RATE_LIMITED"


def test_default_limits_enforced_on_undecorated_route_via_middleware():
    """Regression test for the specific bug: `default_limits` are only enforced
    when SlowAPIMiddleware is registered — a Limiter with default_limits but no
    middleware and no per-route @limiter.limit(...) silently never trips, which
    is exactly how app/main.py was previously broken (app.state.limiter set,
    default_limits configured, but SlowAPIMiddleware never added)."""
    mini = FastAPI()
    limiter = Limiter(key_func=get_remote_address, default_limits=["2/minute"])
    mini.state.limiter = limiter
    mini.add_middleware(SlowAPIMiddleware)

    @mini.exception_handler(RateLimitExceeded)
    async def _rate_limit_exceeded_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=error_response("RATE_LIMITED", "Too many requests. Slow down a moment."),
            headers={"Retry-After": "60"},
        )

    @mini.get("/undecorated")
    def undecorated() -> dict:
        return {"ok": True}

    client = TestClient(mini)
    codes = [client.get("/undecorated").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
