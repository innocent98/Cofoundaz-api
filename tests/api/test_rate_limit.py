import pytest
import slowapi.middleware
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.routing import Match

from app.core import rate_limit
from app.core.config import settings
from app.core.envelope import error_response
from app.core.logger import log
from app.core.rate_limit import (
    find_route_handler,
    install_included_router_support,
    verify_included_router_resolution,
)

# NOTE: importing app.main is what installs the slowapi included-router patch
# (app/main.py calls install_included_router_support() at import time). The mini
# apps below therefore inherit the fix, exactly as the real app does. That is
# deliberate: these tests assert the behaviour the deployed process has.
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

    The interaction is now repaired in-tree by app/core/rate_limit.py, which
    teaches slowapi to descend `_IncludedRouter`, and the fastapi ceiling has
    moved to <0.142.0. This test is what keeps that honest: it asserts the
    OUTCOME (an included route actually gets limited), not the mechanism, so it
    goes red for any future change - in fastapi, in slowapi, or in our own
    adapter - that breaks included-route resolution again.

    Verified to be load-bearing: with the patch disabled on fastapi 0.141.1 this
    returns [200, 200, 200] and fails.
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
        "SlowAPIMiddleware can no longer resolve included routes, so the entire "
        "/api/v1 surface (auth included) is being served UNLIMITED. Check whether "
        "fastapi or slowapi was upgraded; see app/core/rate_limit.py and the "
        "fastapi pin in pyproject.toml."
    )


def test_default_limits_reach_deeply_nested_include_router():
    """The shape app/main.py ACTUALLY has: a router inside a router.

    `app.include_router(api_router, prefix="/api/v1")` and, inside that,
    `api_router.include_router(health.router, prefix="/health")`. On fastapi
    >= 0.137 that is an `_IncludedRouter` nested inside another
    `_IncludedRouter`, and resolving it needs a RECURSIVE descent - a resolver
    that only unwrapped one level would pass the single-level test above and
    still leave every real endpoint unlimited.

    This is the future-proofing case: the single-level test can be satisfied by
    a shallow fix, this one cannot.
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

    leaf = APIRouter()

    @leaf.get("/{item_id}")
    def leaf_endpoint(item_id: int) -> dict:
        return {"ok": True}

    middle = APIRouter()
    middle.include_router(leaf, prefix="/items")
    mini.include_router(middle, prefix="/api/v1")

    client = TestClient(mini)
    codes = [client.get("/api/v1/items/7").status_code for _ in range(3)]
    assert codes == [200, 200, 429], (
        "default_limits did not reach a route nested TWO routers deep. The "
        "included-router resolution in app/core/rate_limit.py is not recursing."
    )


def test_real_app_rate_limits_an_api_v1_route():
    """End-to-end against the REAL app object, not a mini stand-in.

    Every other test here builds its own FastAPI app, which means all of them
    could pass while app/main.py's actual wiring is broken - that is precisely
    how the original bug survived the whole unit suite. This one drives the real
    limiter, the real middleware stack and a real `/api/v1` route mounted two
    routers deep, and pins the exact request number at which the 429 arrives.
    """
    limiter = app.state.limiter
    limit = settings.RATE_LIMIT_PER_MINUTE

    # The limiter's MemoryStorage is process-global and shared with every other
    # test that touches the real app, so leave it exactly as it was found.
    limiter.reset()
    try:
        client = TestClient(app)
        codes = [client.get("/api/v1/health").status_code for _ in range(limit + 1)]
    finally:
        limiter.reset()

    first_429 = codes.index(429) + 1 if 429 in codes else None
    assert first_429 == limit + 1, (
        f"expected the first 429 at request {limit + 1} on /api/v1/health, got "
        f"{first_429!r}. Distinct status codes seen: {sorted(set(codes))}."
    )
    assert set(codes[:limit]) == {200}


def test_resolver_returns_the_endpoint_for_an_included_route():
    """Unit-level check on the resolver itself, independent of middleware."""
    probe_app = FastAPI()
    probe_router = APIRouter()

    @probe_router.get("/thing")
    def thing() -> dict:
        return {"ok": True}

    probe_app.include_router(probe_router, prefix="/prefixed")

    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/prefixed/thing",
        "root_path": "",
        "headers": [],
    }
    assert find_route_handler(probe_app.routes, scope) is thing


def test_unresolvable_matching_route_is_logged_not_swallowed():
    """The one thing this module must never do is go quiet.

    If a route matches the request but no endpoint can be resolved from it,
    slowapi will exempt it and rate limiting is off for that path. That is the
    exact shape of the original bug, so it has to leave a trace an operator can
    find - a bare `return None` is what cost us the first time.

    Asserts the interpolated VALUES land in the message, not just the prefix:
    `log` is loguru, so a %-style call would render the placeholders literally
    and the alert would name neither the method nor the path.
    """

    class _OpaqueRoute:
        """Matches everything, exposes neither `endpoint` nor `_match` - i.e. a
        future FastAPI container this resolver does not understand."""

        def matches(self, scope: dict) -> tuple[Match, dict]:
            return Match.FULL, {}

    scope: dict = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "root_path": "",
        "headers": [],
    }

    captured: list[str] = []
    sink_id = log.add(lambda message: captured.append(str(message)), level="ERROR")
    try:
        assert find_route_handler([_OpaqueRoute()], scope) is None
    finally:
        log.remove(sink_id)

    text = "".join(captured)
    assert "Rate limiting DISABLED" in text
    assert "POST" in text
    assert "/api/v1/auth/login" in text
    assert "_OpaqueRoute" in text


def test_boot_verification_passes_on_the_installed_fastapi():
    """The guard app/main.py runs at import time must be satisfied here."""
    verify_included_router_resolution()


def test_boot_verification_fails_loudly_when_resolution_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a future fastapi breaks resolution, the process must refuse to START.

    Silence is the failure mode that cost us the original bug, so the boot check
    has to raise rather than log-and-continue.
    """
    monkeypatch.setattr(rate_limit, "find_route_handler", lambda routes, scope: None)
    with pytest.raises(RuntimeError, match="Rate limiting is broken"):
        verify_included_router_resolution()


def test_install_fails_loudly_if_slowapi_renames_the_patched_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slowapi upgrade that moves `_find_route_handler` must not leave us
    silently unpatched - the patch would become a no-op and rate limiting would
    quietly die again."""
    monkeypatch.delattr(slowapi.middleware, "_find_route_handler")
    with pytest.raises(RuntimeError, match="_find_route_handler is missing"):
        install_included_router_support()


def test_install_fails_loudly_if_slowapi_changes_the_helper_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slowapi.middleware,
        "_find_route_handler",
        lambda routes, scope, extra: None,
    )
    with pytest.raises(RuntimeError, match="unexpected signature"):
        install_included_router_support()
