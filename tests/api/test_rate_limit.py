from fastapi import APIRouter, FastAPI, Request
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


def test_default_limits_reach_routes_registered_via_include_router():
    """Regression test for the FastAPI 0.137 `_IncludedRouter` breakage.

    The test above proves default_limits work on a route declared DIRECTLY on the
    app. That is not the shape app/main.py actually uses: every real endpoint is
    mounted with `include_router(api_router, prefix="/api/v1")`, and that
    distinction turned out to be the whole bug.

    FastAPI 0.137.0 changed `include_router()` to wrap included routes in an
    internal `_IncludedRouter` container instead of flattening them into
    `app.routes`. slowapi's SlowAPIMiddleware finds the endpoint for the current
    request by iterating `app.routes` and matching entries that expose
    `.endpoint`; `_IncludedRouter` exposes neither, so every included route
    became invisible to the limiter and silently stopped being rate limited -
    the entire /api/v1 surface, auth included - while `/health` (declared with
    @app.get) kept working and every existing test stayed green.

    pyproject pins fastapi <0.137.0 for exactly this reason. This test is what
    makes that pin self-enforcing: raise the ceiling without fixing the
    interaction and this fails, instead of the API silently losing rate limiting.
    """
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

    router = APIRouter()

    @router.get("/via-router")
    def via_router() -> dict:
        return {"ok": True}

    # The load-bearing line: mounted through a router with a prefix, exactly as
    # app/main.py mounts api_router - not declared directly on the app.
    mini.include_router(router, prefix="/api/v1")

    client = TestClient(mini)
    codes = [client.get("/api/v1/via-router").status_code for _ in range(3)]
    assert codes == [200, 200, 429], (
        "default_limits did not reach a route registered via include_router - "
        "SlowAPIMiddleware can no longer resolve included routes. Check whether "
        "fastapi has been upgraded to >=0.137.0 (see the pin in pyproject.toml)."
    )
