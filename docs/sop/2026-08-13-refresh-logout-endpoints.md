# SOP: Refresh + logout endpoints (dual transport)

## What shipped

- Commit: `0281209` — `feat(auth): refresh rotation + logout endpoints`
- Branch: `feat/auth-endpoints`
- Task 10 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-10-brief.md`).

`POST /api/v1/auth/refresh` and `POST /api/v1/auth/logout` — the HTTP surface over the
refresh-token session service shipped in Task 4
(`docs/sop/2026-08-13-refresh-token-session-service.md`). Both endpoints accept the raw
refresh token from either an `httponly` cookie or the JSON body, so mobile (body) and web
(cookie) clients share one contract.

## Why

Login (Task 6) and the service layer (Task 4) already issue and rotate refresh tokens
internally, but nothing exposed rotation or revocation over HTTP — a client had no way to
renew an expiring access token or to log out server-side. This task closes that gap.

## How

**`app/api/v1/endpoints/auth/sessions.py`** (new):

- `_read_refresh(request, payload) -> str` — shared helper: cookie
  (`request.cookies.get(settings.REFRESH_COOKIE_NAME)`) takes precedence, falls back to
  `payload.refresh_token` (`payload` may be `None` — see fix round 1 below), defaults to
  `""` if neither is present. Used by both routes so cookie-vs-body precedence can't drift
  between them.
- `POST /refresh`: reads the raw token via `_read_refresh`; empty → `Unauthorized` (401).
  Otherwise calls `rotate_refresh(db, raw, ip=..., user_agent=...)` — which itself raises
  `Unauthorized` on an already-rotated/revoked/expired token (see Task 4's reuse-detection
  path) — commits, sets the new refresh cookie, and returns
  `{access_token, refresh_token}`.
- `POST /logout`: reads the raw token the same way; if present, `revoke_session` + commit
  (idempotent — a missing/already-revoked token is a no-op, never a 401). Always clears the
  refresh cookie regardless of whether a token was found, and always returns
  `{logged_out: true}` — logout never fails from the client's point of view.

**`app/api/v1/endpoints/auth/__init__.py`** (modified): added
`from app.api.v1.endpoints.auth import ... sessions` and
`router.include_router(sessions.router)`, extending the existing aggregation pattern from
Task 7.

Implemented the brief's code verbatim, including the `-> dict[str, Any]` return
annotations (matches the `login.py`/`registration.py` precedent for `mypy`'s
`disallow_untyped_defs`).

## What's involved

- `app/api/v1/endpoints/auth/sessions.py` (new) — `_read_refresh` helper + `refresh`/
  `logout` routes.
- `app/api/v1/endpoints/auth/__init__.py` (modified) — mounts `sessions.router`.
- `tests/api/auth/test_sessions_endpoints.py` (new, 3 tests) — rotation happy path, reuse
  → 401, logout clears + subsequent refresh with the same token → 401.
- Consumes (no changes): `app/services/auth/sessions.py::rotate_refresh/revoke_session/
  set_refresh_cookie/clear_refresh_cookie`, `app/api/deps.py::Unauthorized`,
  `app/schemas/auth.py::RefreshRequest`, `app/core/envelope.py::success_response`.
- No new DB models, no migration, no config changes.

## Verification

RED (routes not mounted):
```
$ poetry run pytest tests/api/auth/test_sessions_endpoints.py -v
FAILED test_refresh_rotates - assert 404 == 200
FAILED test_refresh_reuse_is_401 - assert 404 == 401
FAILED test_logout_clears - assert 404 == 200
3 failed
```

GREEN:
```
$ poetry run pytest tests/api/auth/test_sessions_endpoints.py -v
3 passed
```

Full suite:
```
$ poetry run pytest -q
89 passed
```
(86 pre-existing + 3 new, no regressions, no unexpected new passes.)

Lint:
```
$ make lint
black --check app tests      -> All done! 92 files would be left unchanged.
isort --check-only app tests -> clean
ruff check app tests         -> All checks passed!
mypy app                     -> Success: no issues found in 52 source files
```

## Operate / roll back

- Pure additive route wiring over an already-shipped, already-tested service layer — no
  migration, no config change. Roll back by reverting `0281209`.
- The refresh cookie is scoped `path="/api/v1/auth"` (set in Task 4's
  `set_refresh_cookie`), so it's only ever sent back to these two endpoints (and `/login`) —
  clearing it on logout is a no-op for any other route regardless.

## Follow-ups

- No per-endpoint rate limit on `/refresh` or `/logout` yet — same acknowledged gap as
  Task 4's SOP; only the app-wide default limiter applies. Revisit if refresh-token
  brute-forcing becomes a concern (entropy is 384 bits per Task 4, so low priority).
- `revoke_all_for_user` ("log out everywhere") exists in the service layer but has no HTTP
  endpoint yet — out of scope for this task, likely a settings/security-page feature later.

## Update — fix round 1: bodyless cookie-only requests returned 422

**What shipped**: `payload: RefreshRequest` was a required body parameter on both routes.
FastAPI validates and rejects a request with no JSON body *before* the handler (and
therefore `_read_refresh`) ever runs, returning 422 `Field required`. This broke the
cookie transport entirely — a browser `fetch(url, {credentials: 'include'})` call that
relies solely on the `httponly` cookie and sends no body never reached the cookie-reading
logic. Caught in code review, not by the original 3 tests (all of which pass an explicit
body).

**Why (root cause)**: declaring a Pydantic model parameter with no default makes FastAPI
treat the request body as required at the OpenAPI/validation layer, independent of what
the handler body does with it. The dual-transport design (cookie *or* body) was only
implemented inside the handler — the route signature itself still demanded a body.

**Fix**: `payload: RefreshRequest | None = None` on both `refresh` and `logout` (moved
after the non-default `request`/`response` params to satisfy Python's argument-ordering
rule). Verified in isolation before touching the endpoint file: a minimal FastAPI route
with this signature returns 200 with `payload=None` on a bodyless POST, 200 with a parsed
model on `json={}`, and 200 with a parsed model on a populated body — so no case
regresses. `_read_refresh` updated to treat `payload=None` the same as
`payload.refresh_token=None` (falls through to the empty-string default). Cookie-first
precedence is unchanged.

**Tests added** (`tests/api/auth/test_sessions_endpoints.py`, all send no `json=` arg):
`test_refresh_cookie_only_no_body`, `test_logout_no_body_no_cookie_is_200`,
`test_refresh_no_body_no_cookie_is_401`. Confirmed each failed against the pre-fix code
first (`git stash` the fix, re-run — all 3 failed with the reported 422/`Field required`),
then passed after the fix.

**Test-infra note**: `test_refresh_cookie_only_no_body` sets the refresh cookie explicitly
via `client.cookies.set(settings.REFRESH_COOKIE_NAME, refresh, path="/api/v1/auth")`
rather than relying on the `TestClient`'s cookie jar to auto-carry the `Set-Cookie` from
the preceding `/login` call. Investigated why: httpx 0.27's cookie jar stores a cookie
from a single-label host (`testserver`, the `TestClient` default) under domain
`testserver.local`, and the domain-matching check on the next outgoing request silently
fails to reattach it — a jar quirk unrelated to this endpoint's own cookie-reading code,
confirmed with a standalone repro script before deciding to set the cookie explicitly in
the test instead of chasing the jar behavior.

**Files touched**: `app/api/v1/endpoints/auth/sessions.py` (`payload` made optional on
both routes, `_read_refresh` handles `None`), `tests/api/auth/test_sessions_endpoints.py`
(+3 tests, +`settings` import).

**Verification**:
```
$ poetry run pytest tests/api/auth/test_sessions_endpoints.py -v
6 passed
```
```
$ poetry run pytest -q
92 passed
```
(89 pre-fix + 3 new, no regressions.)
```
$ make lint
black/isort/ruff/mypy all clean (52 source files)
```
