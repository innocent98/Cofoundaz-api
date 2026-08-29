"""Makes slowapi's rate limiting survive FastAPI >= 0.137's `_IncludedRouter`.

THE BUG THIS EXISTS TO FIX
--------------------------
`SlowAPIMiddleware` decides whether to rate limit a request by resolving the
endpoint for it, via `slowapi.middleware._find_route_handler(app.routes, scope)`.
That helper iterates `app.routes`, keeps entries where `route.matches(scope)` is
`Match.FULL`, and reads `route.endpoint` off them.

FastAPI 0.137.0 stopped flattening `include_router()` routes into `app.routes`.
They are now hidden behind a single `_IncludedRouter` container which reports
`path=None` and exposes NO `.endpoint`. So `_find_route_handler` returns None for
them, and slowapi's `_should_exempt()` treats "no handler" as "exempt this
request" - every route mounted via `include_router` silently stops being rate
limited. For this app that is the whole `/api/v1` surface, auth included, while
`/health` (declared with `@app.get`) keeps working. That asymmetry is why the
test suite did not notice: every pre-existing rate-limit test used routes
declared directly on the app.

WHY WE CARRY THIS OURSELVES
---------------------------
Upstream has not shipped a fix. slowapi 0.1.10 (2026-06-13, the latest release)
still contains the broken `_find_route_handler`. The breakage is tracked in
laurentS/slowapi#281 with three competing UNMERGED PRs (#282, #285, #286). This
module is a local stand-in until one of those lands in a release; when it does,
delete this module and bump slowapi.

WHY NOT `request.scope["route"]`
--------------------------------
Starlette does set the matched route into `scope["route"]`, which would be a far
more robust signal than scanning `app.routes`. It is not usable here: routing
happens INSIDE the app, downstream of the middleware stack, so `scope["route"]`
is still absent when a middleware runs its pre-dispatch check. Measured on
fastapi 0.141.1 - `scope["route"]` is absent before `call_next` for EVERY route,
`/health` included. A limiter must decide before the endpoint executes, so it
cannot wait for it.

HOW THIS BREAKS, AND HOW YOU WILL KNOW
--------------------------------------
`_resolve_included_endpoint` calls `_IncludedRouter._match()`, a FastAPI private
API. A future FastAPI release may rename or reshape it. Two guards make that
loud instead of silent - which is the entire point, since the original bug cost
us nothing but silence:

1. `verify_included_router_resolution()` runs at import time in `app/main.py`
   and refuses to start the process if a route mounted through `include_router`
   cannot be resolved. A broken FastAPI upgrade fails at boot, not in prod at
   3am with rate limiting quietly off.
2. `install_included_router_support()` asserts slowapi still exposes
   `_find_route_handler` with the signature we are replacing, and raises if not
   - so a slowapi upgrade that renames it cannot leave us silently unpatched.

`tests/api/test_rate_limit.py` is the third guard and the one that gates CI.
"""

import inspect
from collections.abc import Callable, Iterable, MutableMapping
from typing import Any

import slowapi.middleware
from starlette.routing import BaseRoute, Match

from app.core.logger import log

# Depth cap for descending nested `include_router` chains. Routers nested more
# than this deep are pathological; the cap exists so a future FastAPI structure
# that self-references cannot spin the resolver.
_MAX_ROUTER_DEPTH = 20

Scope = MutableMapping[str, Any]
Endpoint = Callable[..., Any]


def _resolve_included_endpoint(route: Any, scope: Scope, depth: int = 0) -> Endpoint | None:
    """Descend a FastAPI `_IncludedRouter` to the concrete endpoint for `scope`.

    Delegates the actual matching to the container's own `_match()` rather than
    re-implementing path/prefix matching. That matters: an included route's
    `original_route.path` is UNPREFIXED ('/via-router', not
    '/api/v1/via-router'), and FastAPI resolves the effective prefixed path
    through context it stashes in the scope while matching. Re-deriving that
    here would be a second implementation of FastAPI's router, guaranteed to
    drift. Using `_match()` means we match exactly as the real dispatch will.
    """
    match_fn = getattr(route, "_match", None)
    if depth > _MAX_ROUTER_DEPTH or not callable(match_fn):
        return None

    result = match_fn(scope)
    if not isinstance(result, tuple) or len(result) != 4:
        return None

    match, _child_scope, matched_route, route_context = result
    if match != Match.FULL:
        return None

    # `_match` hands back an `_EffectiveRouteContext` carrying the endpoint for
    # a leaf APIRoute, or the nested `_IncludedRouter` itself for a subtree.
    endpoint = getattr(route_context, "endpoint", None) or getattr(matched_route, "endpoint", None)
    if endpoint is not None:
        return endpoint  # type: ignore[no-any-return]

    if matched_route is not None and matched_route is not route:
        return _resolve_included_endpoint(matched_route, scope, depth + 1)
    return None


def find_route_handler(routes: Iterable[BaseRoute], scope: Scope) -> Endpoint | None:
    """Drop-in replacement for `slowapi.middleware._find_route_handler`.

    Keeps upstream's last-full-match-wins semantics, and adds a descent into
    `_IncludedRouter` containers for FastAPI >= 0.137. Routes that expose
    `.endpoint` directly (`@app.get`, and every route on FastAPI < 0.137) take
    the same path they always did, so this is a superset of upstream behaviour.
    """
    handler: Endpoint | None = None
    unresolved: BaseRoute | None = None

    for route in routes:
        match, _child_scope = route.matches(scope)
        if match != Match.FULL:
            continue

        endpoint: Endpoint | None = getattr(route, "endpoint", None)
        if endpoint is not None:
            handler = endpoint
            continue

        # A container (FastAPI >= 0.137 `_IncludedRouter`). Probe against a
        # shallow copy: `_match` writes bookkeeping keys into the scope it is
        # given, and this is the live request scope.
        resolved = _resolve_included_endpoint(route, dict(scope))
        if resolved is not None:
            handler = resolved
        else:
            unresolved = route

    if handler is None and unresolved is not None:
        # Exactly the shape of the original bug: a route matched, but no
        # endpoint came back, so slowapi is about to exempt it from rate
        # limiting. Never let that pass quietly.
        # f-string, not %-args: `log` is loguru, which formats with str.format
        # and would silently drop %-style arguments - leaving the one log line
        # that is supposed to be loud saying nothing useful.
        log.error(
            f"Rate limiting DISABLED for {scope.get('method', '?')} "
            f"{scope.get('path', '?')}: matched {type(unresolved).__name__} but could "
            "not resolve its endpoint. FastAPI's included-router internals have "
            "changed - see app/core/rate_limit.py."
        )

    return handler


def install_included_router_support() -> None:
    """Patch slowapi's endpoint resolution in place.

    `_find_route_handler` is a module-level function that
    `SlowAPIMiddleware.dispatch` resolves from its module globals on every call,
    so replacing the module attribute fixes both `SlowAPIMiddleware` and
    `SlowAPIASGIMiddleware` without subclassing either or copying their dispatch
    logic (which would couple us to far more of slowapi's internals than this
    one function does).

    Raises:
        RuntimeError: if slowapi no longer exposes the function we are replacing
            with the signature we expect. A rename upstream must break loudly
            here rather than leave the patch silently inert.
    """
    original = getattr(slowapi.middleware, "_find_route_handler", None)
    if not callable(original):
        raise RuntimeError(
            "slowapi.middleware._find_route_handler is missing. The included-router "
            "rate-limiting patch in app/core/rate_limit.py targets that function; "
            "slowapi's internals have changed. Re-check whether slowapi has fixed "
            "laurentS/slowapi#281 upstream - if so, delete this module."
        )

    params = list(inspect.signature(original).parameters)
    if len(params) != 2:
        raise RuntimeError(
            "slowapi.middleware._find_route_handler has an unexpected signature "
            f"{params!r}; expected two positional parameters (routes, scope). "
            "The patch in app/core/rate_limit.py may no longer be correct."
        )

    # Reaching into slowapi's private module attribute IS the mechanism here,
    # and the guards above are what make that safe to do.
    slowapi.middleware._find_route_handler = find_route_handler  # pylint: disable=protected-access


def verify_included_router_resolution() -> None:
    """Prove, at boot, that a route mounted via `include_router` still resolves.

    This is the guard that turns a silent security regression into a refusal to
    start. It builds a throwaway app (no I/O, no network, microseconds) shaped
    exactly like `app/main.py` - a router mounted under a prefix - and asserts
    the resolver finds its endpoint.

    Raises:
        RuntimeError: if resolution fails, meaning rate limiting would be off
            for the entire `/api/v1` surface.
    """
    # Imported here, not at module scope: this is boot-time verification
    # scaffolding, not part of the request path.
    from fastapi import APIRouter, FastAPI  # pylint: disable=import-outside-toplevel

    probe_app = FastAPI()
    probe_router = APIRouter()

    @probe_router.get("/probe")
    def _probe_endpoint() -> dict[str, bool]:  # pragma: no cover - never called
        return {"ok": True}

    probe_app.include_router(probe_router, prefix="/probe-prefix")

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/probe-prefix/probe",
        "root_path": "",
        "headers": [],
    }
    resolved = find_route_handler(probe_app.routes, scope)

    if resolved is not _probe_endpoint:
        import fastapi  # pylint: disable=import-outside-toplevel

        raise RuntimeError(
            "Rate limiting is broken: slowapi cannot resolve endpoints for routes "
            f"mounted via include_router on fastapi {fastapi.__version__}. Every "
            "/api/v1 route - auth included - would serve unlimited requests. "
            "Refusing to start. See app/core/rate_limit.py and the fastapi pin in "
            "pyproject.toml."
        )
