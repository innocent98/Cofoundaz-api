# SOP — Fix: `GET /jobs/{job_id}` was unauthenticated (tenancy gap)

> **Type:** security fix · **Date:** 2026-08-26 · **Area:** jobs endpoint, tenancy

## What shipped

`GET /api/v1/jobs/{job_id}` now requires a **verified user** and returns the job only to an
**active member of the job's workspace**; unknown / orphan / cross-workspace jobs all return a
uniform `404`. Surfaced by the architecture blueprint's drift-detection pass.

## Why

The endpoint had only `Depends(get_db)` — **no auth, no tenancy** (`jobs.py:16`). Any caller
with a job UUID could read its `status`/`result`/`error`, across tenants. Severity was low today
(UUIDv4 ids are unguessable and current job payloads are trivial), but it is an unauthenticated,
cross-tenant-readable endpoint and must be closed before any job carries sensitive result data.

## How

- Added `get_verified_user` + a membership check against the **job's own `startup_id`**. Chose
  *not* to use `require_workspace` (which needs the `X-Workspace-Id` header): the FE polls jobs
  right after onboarding-complete, before it has a workspace header, so scoping on the job's
  `startup_id` is both correct and header-independent.
- **Uniform 404** for unknown job, orphan job (`startup_id is None`), and cross-workspace — no
  existence probing.

## What's involved

| File | Change |
|---|---|
| `app/api/v1/endpoints/jobs.py` | verified-user + member-of-job's-workspace gate; uniform 404 |
| `tests/api/test_jobs_endpoint.py` | rewritten: 401 unauth · member 200 · cross-workspace 404 · missing 404 · orphan 404 |
| `e2e/test_onboarding.py` | job-status poll now sends the auth header (`:100`) |
| `scripts/e2e_run.sh` | stale default `E2E_PG_PORT` `5433 → 5432` (leftover from the docker port fix) |
| `docs/architecture/system-architecture.md` | blueprint reconciled — finding marked resolved |

## Verification

- `tests/api/test_jobs_endpoint.py` — 5/5 pass (unauth 401, member 200, cross-workspace 404,
  missing 404, orphan 404).
- Full unit suite **331 passed**, ~98% coverage; black/isort/ruff/mypy clean.
- `make e2e` **25/25** (onboarding journey polls jobs with auth and still sees the two job ids /
  per-type statuses).

## Operate / roll back

- No migration. Roll back by reverting `jobs.py`.
- Behaviour change for API consumers: **callers must now send a Bearer token** to poll job status.

## Follow-ups

- Other blueprint findings remain open (roadmap status has no transition guard; dead enum states;
  concurrent double-apply). Tracked in `docs/architecture/system-architecture.md` §8.
