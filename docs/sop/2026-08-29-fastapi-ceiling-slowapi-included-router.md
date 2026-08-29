# SOP — Raising the FastAPI ceiling by fixing slowapi's included-router blindness

> **Type:** dependencies / security control · **Date:** 2026-08-29 · **Area:** `app/core/rate_limit.py`, `app/main.py`, `pyproject.toml`, `tests/api/test_rate_limit.py`

## What shipped

Unblocks Dependabot PR #20 (`production-minor` group), which was held up by one failing test.

| Change | What |
|---|---|
| `app/core/rate_limit.py` (new) | Teaches slowapi to resolve endpoints behind FastAPI's `_IncludedRouter`, plus two loud-failure guards |
| `app/main.py` | Installs the patch and runs the boot-time self-check before `SlowAPIMiddleware` is added |
| `pyproject.toml` | fastapi ceiling `<0.137.0` → `<0.142.0`; uvicorn `^0.32.0` → `^0.52.4`; httpx `^0.27.0` → `^0.28.1` |
| `poetry.lock` | fastapi 0.136.3 → 0.141.1, uvicorn 0.32.1 → 0.52.4, httpx 0.27.2 → 0.28.1, python-dotenv 1.2.2 → 1.2.3 |
| `tests/api/test_rate_limit.py` | 4 tests → 12; adds nested-router, real-app, resolver and guard coverage |

## Why

### The pin existed for a real reason

FastAPI 0.137.0 stopped flattening `include_router()` routes into `app.routes`, hiding them
behind an `_IncludedRouter` container that reports `path=None` and exposes no `.endpoint`.
slowapi's `SlowAPIMiddleware` resolves the endpoint for a request via
`_find_route_handler(app.routes, scope)`, which matches on `hasattr(route, "endpoint")`. It
therefore returned `None` for every included route, and slowapi's `_should_exempt()` treats
"no handler" as "exempt" — so **the entire `/api/v1` surface, auth included, silently stopped
being rate limited** while `/health` (declared with `@app.get`) kept working.

`pyproject.toml` pinned `<0.137.0` and
`tests/api/test_rate_limit.py::test_default_limits_reach_routes_registered_via_include_router`
made that pin self-enforcing. That test is what failed on PR #20 — it was doing its job.

### Upstream has not fixed it

Checked first, because a dependency bump would have been the best outcome:

| Question | Answer |
|---|---|
| Latest slowapi release | **0.1.10**, uploaded 2026-06-13 |
| Does it fix this? | **No.** `_find_route_handler` is byte-for-byte the broken version |
| What changed in 0.1.9 → 0.1.10 | `exempt_when` gaining a `Request` arg, `logger.warn` → `warning`, formatting. Nothing router-related |
| Upstream tracking | `laurentS/slowapi#281` (open, 2026-07-17) with **three competing unmerged PRs**: #282, #285, #286 |

So there is nothing to upgrade to. Option 3 (hold fastapi, let the other three move) was
available but strictly worse: it leaves a known-good upgrade path unused and the ceiling
frozen indefinitely behind a third-party maintainer's merge queue.

### Why not `request.scope["route"]`

Starlette sets the matched route into `scope["route"]`, which would be far more robust than
scanning `app.routes`. **It is not usable from a middleware.** Routing happens inside the
app, downstream of the middleware stack, so the key is not yet present when a pre-dispatch
check runs. Measured on fastapi 0.141.1: `scope["route"]` is absent before `call_next` for
*every* route, `/health` included. A limiter must decide before the endpoint executes, so it
cannot wait for it. `_IncludedRouter.matches()` was also checked — it returns `Match.FULL`
with an **empty** child scope, so the public `BaseRoute.matches` contract yields nothing
either.

## How

`app/core/rate_limit.py` replaces `slowapi.middleware._find_route_handler` with a superset:
routes exposing `.endpoint` take the original path; a matching container is descended via
`_IncludedRouter._match(scope)`.

**Key decision — delegate matching, don't re-implement it.** An included route's
`original_route.path` is *unprefixed* (`/via-router`, not `/api/v1/via-router`); FastAPI
resolves the effective path through context it stashes in the scope while matching.
Re-deriving that would be a second implementation of FastAPI's router, guaranteed to drift.
`_match()` matches exactly as real dispatch will. The descent is **recursive**, which is not
optional: the real app is two routers deep
(`app` → `api_router` → `health.router`), and a one-level unwrap would pass the original
regression test while leaving every real endpoint unlimited.

**Alternatives rejected:**

| Option | Why not |
|---|---|
| Subclass `SlowAPIMiddleware` and reimplement `dispatch` | Couples us to far more slowapi private surface (`sync_check_limits`, `_should_exempt`, `_inject_headers`, `request.state.view_rate_limit`) than the one function actually broken |
| Append synthetic matcher routes to `app.routes` | Relies on Starlette taking the *first* full match while slowapi takes the *last*. Too clever, and a live routing hazard |
| Move rate limiting into a router-level dependency | Runs after body parsing, loses header injection, changes observable behaviour |
| A FastAPI opt-out flag | None exists — checked the 0.141.1 source |

### Making it fail loudly

The original bug cost nothing but silence, so the fix is guarded three ways:

1. **`verify_included_router_resolution()`** runs at import in `app/main.py` and raises, refusing to start the process, if a route mounted through `include_router` cannot be resolved. A broken FastAPI upgrade fails at boot, not in prod with rate limiting quietly off.
2. **`install_included_router_support()`** asserts slowapi still exposes `_find_route_handler` with a two-parameter signature, so a rename upstream cannot leave the patch silently inert.
3. **Per-request `log.error`** when a route matches but yields no endpoint — the exact shape of the original bug — naming the method, path and container type.

The ceiling stays **bounded** (`<0.142.0`) on purpose: the fix leans on FastAPI internals, so
0.142 should arrive as its own reviewed Dependabot PR that must pass these gates.

> A defect caught while writing the guard test: the loud log line was originally written with
> `%s`-style args. `log` is **loguru**, which formats with `str.format` and silently drops
> `%`-args — the one message meant to be loud would have named neither method nor path. It is
> now an f-string, and the test asserts the interpolated values, not just the prefix.

## What's involved

| Path | Note |
|---|---|
| `app/core/rate_limit.py` | New. Resolver, patch installer, boot self-check |
| `app/main.py:95-96` | `install_…()` + `verify_…()` immediately before `app.add_middleware(SlowAPIMiddleware)` |
| `pyproject.toml` | fastapi/uvicorn/httpx constraints, each with its rationale inline |
| `tests/api/test_rate_limit.py` | 12 tests |

`uvicorn.workers.UvicornWorker` (the production `CMD`'s worker class, deprecated back in
uvicorn 0.30) **still ships in 0.52.4** — verified by import and by `make image-check`.
`httpx` is only reached transitively via starlette's `TestClient`; no app code constructs a
client, so 0.28's `proxies=`/`app=` removals do not apply.

## Verification

Measured, not assumed. Rate limit is 120/minute; the driven route is `/api/v1/health`, mounted
two routers deep.

### Live, before vs after (identical fastapi 0.141.1, uvicorn, 130 requests)

| Key shape | Without the fix | With the fix |
|---|---|---|
| Authenticated user (`user:<sub>`) | **0 × 429** (130 × 200) | **first 429 at request 121** |
| Anonymous IP | **0 × 429** (130 × 200) | **first 429 at request 121** |

Both key shapes matter because `_rate_limit_key` produces both.

### Under gunicorn, production image, its own `CMD`

slowapi's storage is in-memory `MemoryStorage`, so buckets are **per worker** and the
effective limit is workers × limit.

| Workers | Requests driven | Result |
|---|---|---|
| 1 | 130 | first 429 at **121** (both key shapes) — matches uvicorn exactly |
| 4 | 700 | **480 × 200** for the user key = exactly 4 × 120 |

At 4 workers the *first* 429 arrives at 373 (user) / 327 (anon), earlier than the aggregate
ceiling, because the kernel distributes accepts unevenly and one worker exhausts its own 120
before the others. The aggregate — 480 — is the number that confirms the model. The anon run
totalled 460, i.e. one worker received under its full 120 within the 700 requests.

**Operational consequence:** the advertised 120/minute is really up to `WEB_CONCURRENCY × 120`
in production (4 × 120 = 480). That is pre-existing behaviour, not a regression, but it is now
measured. Moving slowapi to Redis storage would make the limit global — see Follow-ups.

### Test suite is still load-bearing

With the patch disabled on fastapi 0.141.1, **3 of the 12 tests fail**:
`…via_include_router` ([200,200,200]), `…deeply_nested_include_router`, and
`…real_app_rate_limits_an_api_v1_route`. The regression test was not weakened — its assertion
is unchanged; only its now-stale guidance message was corrected.

### Gates

| Gate | Result |
|---|---|
| `black` / `isort` / `ruff` | clean |
| `mypy app` | no issues, 98 files |
| `pylint app --fail-under=9.5` | **9.94** (unchanged from baseline; no messages from the new module) |
| `bandit -r app/` | exit 0 |
| `poetry check --lock` | exit 0 |
| `pytest --cov-fail-under=95` | **418 passed**, 98.29% (baseline 410 passed, 98.28%) |
| e2e | **27 passed** |
| `make scan` | exit 0 |
| `make image-check` | passed — 3 workers, healthy, graceful stop exit 0 in 1s |

Baseline before any change was 410 passed / 98.28%. Nothing previously green went red.

## Operate / roll back

No schema change, no migration, no config change. Roll back by reverting the commit;
`pyproject.toml` returns to `<0.137.0` and the lock to fastapi 0.136.3.

**Deleting this module later:** when slowapi merges one of #282/#285/#286 and cuts a release,
drop `app/core/rate_limit.py`, remove the two calls in `app/main.py`, bump slowapi, and keep
every test in `tests/api/test_rate_limit.py` — they assert outcomes, not our mechanism, so
they validate the upstream fix unchanged.

## Follow-ups

- **Per-worker limits.** `MemoryStorage` means the real ceiling is `WEB_CONCURRENCY ×
  RATE_LIMIT_PER_MINUTE`. Redis is already a dependency; pointing slowapi's storage at it
  would make the limit global and exact. Worth doing before the limit is relied on as a
  security control rather than an abuse damper.
- **Watch `laurentS/slowapi#281`.** Once a fixed release exists, prefer it over this module.
- **fastapi 0.142.** Will arrive as its own Dependabot PR. The boot check and the test suite
  are the gate; do not raise the ceiling without both passing.
