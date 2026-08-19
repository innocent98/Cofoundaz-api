# SOP — Onboarding (Plan 3)

**What shipped** — The full founder onboarding wizard: a draft-workspace model, a 4-step
state machine (`GET`/`PATCH /onboarding/state`), logo upload, teammate invitations
(create/preview/accept, email-bound), and `POST /onboarding/complete` which gates on
required fields and dispatches two stub background jobs. New `invitations` table plus two
schema changes to `startups`/`startup_profiles` (migration `0003_onboarding`). This is the
**single consolidated SOP for the whole onboarding feature** — per-task SOPs were
intentionally deferred here (branch `feat/onboarding`, 10 implementation tasks + this one).

Commits: `a569938`..`adf1d4f` (Tasks 1–10) + this task's `e2e/test_onboarding.py` /
`docs/sop/2026-08-15-onboarding.md` commit.

## Why

Plan 2 shipped auth (signup/verify/login/MFA) but a verified user had nowhere to go — no
workspace, no way to invite a co-founder, no signal to the rest of the product (roadmap,
health score) that a startup exists. Plan 3's goal: let a founder go from "just verified"
to "workspace exists, teammate invited and joined, roadmap/health-score generation queued"
in one wizard, without inventing a second "pending signup" state machine — the workspace
itself *is* the draft.

## How

**Draft-workspace model.** There is no separate "onboarding session" table. The first
`GET /onboarding/state` call lazily creates a real `Startup` row (`name=NULL`) + an active
`founder` `Membership`, via `resolve_or_create_workspace()`
(`app/services/onboarding/workspace.py:14`). Each wizard step is a `PATCH` that writes
directly onto that startup / its profile / the user's profile — there's nothing to migrate
when onboarding finishes, because the row being edited during onboarding *is* the
production workspace row. `startups.name` had to become nullable (migration `0003`) to
allow this pre-named draft state.

**4-step wizard, one PATCH endpoint.** `OnboardingStatePatch` (`app/schemas/onboarding.py`)
carries all four steps' fields as optionals in one schema; `apply_step()`
(`app/services/onboarding/steps.py`) is a plain autosave — it writes *every* field the
client actually set (`exclude_unset=True`) to its mapped target (founder profile /
startup / startup profile), regardless of which `step` was sent. `step` itself is used
only to advance `onboarding_step`, the resumable-wizard cursor (`max(current, patch.step)`,
so it never regresses) — it does not gate which fields get written. Grouping fields into
"step 1 = founder profile, step 2 = startup name…" is a client-side UI/UX convention, not
something the server enforces; a client is free to send `{"step": 1, "name": "..."}` and
`name` will be written. Field-level correctness is pydantic's job: enums are validated
(`business_model`, `stage`), and `goals` is capped at 3 items. `goals` and `role_title` are
treated as non-nullable-if-present: an explicit `{"goals": null}` is a no-op rather than a
`NOT NULL` constraint violation (fixed in Task 5 after a live-reproduced 500).

**Logo upload** (`POST /onboarding/logo`) streams the multipart body in 64 KB chunks with
an early abort past `_MAX_BYTES` (2 MB) so an oversized upload can't buffer unbounded
memory before the size check fires; accepts PNG/JPEG/SVG/WebP.

**Invitations are email-bound, not user-bound.** `create_invitations()`
(`app/services/onboarding/invites.py`) writes a row per invite with a hashed token
(`token_hash`, raw token only ever in the email body) and a 14-day expiry; the invite is
addressed to an email, not an account. `GET /invitations/{token}` is a public,
unauthenticated preview (startup name, role, inviter name — no auth, no leakage of other
fields). `POST /invitations/accept` requires the caller to be logged in **as a user whose
own verified email matches the invitation's email** (`InviteEmailMismatch` 403 otherwise)
— this is the core security property: accepting an invite is not "anyone with the link
joins," it's "the specific invited email joins." Accept is idempotent (existing membership
returned as-is) rather than erroring on repeat accept.

Within one `POST /invites` call, in-request duplicate emails are deduped via an in-memory
`handled_this_call` set rather than a DB re-query — prod runs `autoflush=False`
(`app/db/session.py`), so a same-request `Invitation` add wouldn't be visible to a query
yet; Task 7 found and fixed this live (test fixture's `autoflush=True` had hidden it).

**Complete → gate → two stub jobs.** `POST /onboarding/complete`
(`app/services/onboarding/complete.py`) checks five required fields (full name, startup
name, industry, stage, ≥1 goal) and returns `422 ONBOARDING_INCOMPLETE` with
`field_errors` naming each missing one if any are absent. On success it's idempotent
(repeat calls return `job_ids: []`), stamps `onboarding_completed_at` +
`assessment_pending=True`, and enqueues two jobs via the v1 `JobDispatcher` stub
(`app/platform/jobs.py`) — `roadmap.generate` and `healthscore.initialize` — both
persisted as `status=queued` `Job` rows, pollable at `GET /jobs/{id}`. No worker drains
them yet (Modules 05/06); this task proves the dispatch contract, not execution.

**Events.** `workspace.created`, `workspace.member.invited`, `workspace.member.joined`,
`onboarding.completed`, `notification.onboarding_complete` are published via the existing
`event_bus` at each step (currently a log-only stub — Module 20 makes it real).

## What's involved

**Data model / migration**
- `alembic/versions/0003_onboarding.py` — new `invitations` table (+ `ix_invitations_email`,
  `ix_invitations_startup_id`, `uq_invitations_token_hash`); `startups.name` `DROP NOT NULL`;
  `startup_profiles.assessment_pending BOOLEAN NOT NULL DEFAULT true` (metadata-only,
  Postgres 11+ fast-path — no table rewrite even on populated tables).
- `app/db/models/invitation.py` — `Invitation` model (`startup_id`, `email` CITEXT,
  `role`, `token_hash` unique, `status`, `invited_by`, `expires_at`, `accepted_at`,
  `accepted_user_id`).
- `app/db/models/startup.py` — `Startup.name` now `str | None`;
  `StartupProfile.assessment_pending`.

**Endpoints**
| Method | Path | Auth | File |
|---|---|---|---|
| GET | `/api/v1/onboarding/state` | verified user | `app/api/v1/endpoints/onboarding/state.py` |
| PATCH | `/api/v1/onboarding/state` | verified user | `app/api/v1/endpoints/onboarding/state.py` |
| POST | `/api/v1/onboarding/logo` | verified user | `app/api/v1/endpoints/onboarding/logo.py` |
| POST | `/api/v1/onboarding/invites` | verified user | `app/api/v1/endpoints/onboarding/invites.py` |
| POST | `/api/v1/onboarding/complete` | verified user | `app/api/v1/endpoints/onboarding/complete.py` |
| GET | `/api/v1/invitations/{token}` | none (public preview) | `app/api/v1/endpoints/invitations.py` |
| POST | `/api/v1/invitations/accept` | verified user, email-bound | `app/api/v1/endpoints/invitations.py` |
| GET | `/api/v1/jobs/{job_id}` | none (dev-visibility stub) | `app/api/v1/endpoints/jobs.py` |

**Services / schemas / errors**
- `app/services/onboarding/workspace.py` — `resolve_or_create_workspace`, `serialize_state`.
- `app/services/onboarding/steps.py` — `apply_step`.
- `app/services/onboarding/invites.py` — `create_invitations`, `preview_invitation`,
  `accept_invitation`.
- `app/services/onboarding/complete.py` — `complete_onboarding`, `_gate`.
- `app/schemas/onboarding.py` — `OnboardingStatePatch`, `InviteItem`, `InvitesRequest`,
  `AcceptRequest`.
- `app/core/errors.py` — `OnboardingIncomplete` (422), `OnboardingAlreadyComplete` (409,
  fix wave below), `InviteEmailMismatch` (403), `AlreadyMember` (409, defined for future
  use — accept is currently idempotent rather than erroring), `EmailNotVerified` (403).
- `app/api/deps.py` — `get_verified_user` (wraps `get_current_user`, gates on
  `email_verified_at`).
- `app/platform/jobs.py` — `JobDispatcher.enqueue` (v1 stub: persists `queued`, no worker).

**Tests**
- `tests/` — 143 unit tests across all onboarding services/endpoints (rolled-back real-DB
  transactions per the project convention, no DB mocking).
- `e2e/test_onboarding.py` (this task) — one live end-to-end journey against a real
  running server.

## Verification

- **Live E2E: 19 passed** (`make e2e`) — 11 prior auth journeys + 7 smoke + the new
  `test_full_onboarding_journey` (founder signup→verify→login→wizard steps 1–4→invite→
  teammate signup-with-invited-email→verify→accept→`/auth/me` shows membership→founder
  completes→2 `queued` jobs pollable at `GET /jobs/{id}`).
- **Unit suite: 143 passed, 97% coverage** (`poetry run pytest -q`); `make lint` clean
  (black, isort, ruff, mypy all pass on `app`/`tests`).
- Migration round-trip verified in Task 2 (`alembic upgrade head && downgrade -1 && upgrade
  head`) and again implicitly by every `make e2e` run (fresh `cofoundaz_e2e` DB migrated
  from zero each time).

## Operate

- No new env vars or deploy steps beyond the existing `make e2e` / `alembic upgrade head`
  flow. Jobs table already exists from Plan 1 (`0001_initial_schema`); no new job types
  need worker registration yet since nothing drains the queue in this plan.
- **Rollback:** `alembic downgrade -1` drops `invitations`, drops
  `startup_profiles.assessment_pending`, and re-adds `startups.name NOT NULL`. The
  migration's own `downgrade()` docstring flags the one real risk: if any startup row was
  left with `name IS NULL` (an incomplete draft onboarding) at downgrade time, the
  `SET NOT NULL` step fails — intentionally, surfacing the data problem rather than
  silently truncating rows. Complete or delete draft workspaces before downgrading past
  `0003` in an environment with real onboarding traffic.

## Follow-ups

**Deferred to later modules (by design, not oversights):**
- **AI panel** (Module 03) — no AI-assisted suggestions in the wizard yet.
- **Real notifications** (Module 20) — `event_bus` is a log-only stub; `workspace.member.invited` /
  `onboarding.completed` etc. publish events nothing currently consumes.
- **Assessment flow** (Module 07) — `assessment_pending=True` is set on complete but
  nothing yet clears it; the flag exists so the assessment module has a signal to key off.
- **Job execution** (Modules 05/06) — `roadmap.generate` / `healthscore.initialize` are
  persisted as `queued` and never move to `running`/`done`; no worker exists yet.
- **`healthscore.initialize` stub retired (Module 06)** — as of Module 06, the
  `healthscore.recalculate` / `healthscore.initialize` stub jobs are retired; the Health
  Score is recomputed inline at assessment-complete instead (see
  `docs/sop/2026-08-19-health-score.md`).

**Parked during review (deliberate, tracked risk — not blocking, revisit if traffic grows):**
- **Concurrent workspace-create race** (Task 3): `resolve_or_create_workspace` is
  SELECT-then-INSERT with no per-user lock or unique constraint. Two simultaneous first
  `GET /state` calls for the same user could each create a startup; worst case is a rare
  orphan empty draft workspace, resolved by "most recent" ordering downstream, no
  security/data-loss impact. Proper fix: `pg_advisory_xact_lock(hashtext(user_id))` around
  the resolve-or-create + a 2-connection concurrency test.
- **Concurrent double-accept IntegrityError** (Task 10): two simultaneous
  `POST /invitations/accept` calls for the same user+invite can race past the
  existing-membership check and both attempt an insert; the `uq_memberships` unique
  constraint prevents any duplicate membership (no data corruption) but the loser gets an
  unhandled `IntegrityError` → 500 instead of the same idempotent 200. Cheap fix available:
  catch `IntegrityError`, re-select, return the row.
- **Re-accept doesn't reactivate a removed/suspended member** (Task 10): accepting an
  invitation when a `Membership` row already exists (e.g. previously removed) returns the
  existing row as-is rather than reactivating it or updating its role — out of scope for
  onboarding (belongs to Module 23 Team Collab), but worth noting: a removed-then-reinvited
  member currently gets a 200 with stale (removed) access rather than being restored.

## Fix wave — 2026-08-15 (final whole-branch review)

Two behavioral fixes shipped from the final `feat/onboarding` review, plus the `apply_step`
wording correction above.

**Guard mutating endpoints against an already-completed workspace.** `PATCH
/onboarding/state`, `POST /onboarding/logo`, and `POST /onboarding/invites` previously kept
resolving to and mutating the workspace even after `POST /onboarding/complete` had stamped
`onboarding_completed_at` — a founder could rename the startup, re-upload a logo, or add
invites post-completion, which onboarding (draft-only) should reject. Added
`ensure_draft(startup)` (`app/services/onboarding/workspace.py`), called immediately after
`resolve_or_create_workspace(...)` in all three mutating endpoints
(`app/api/v1/endpoints/onboarding/state.py`, `logo.py`, `invites.py`); it raises the new
`OnboardingAlreadyComplete` error (`app/core/errors.py`, `ONBOARDING_ALREADY_COMPLETE`, 409)
when `startup.profile.onboarding_completed_at is not None`. `GET /state` and `POST
/complete` are intentionally left unguarded — `GET` must keep working post-completion
(`completed: true`), and `complete` already has its own idempotency check. Tests:
`test_patch_after_completion_is_409`, `test_logo_upload_after_completion_is_409`,
`test_invites_after_completion_is_409`.

**`preview_invitation` now rejects expired/non-pending tokens (404), matching `accept`.**
`GET /invitations/{token}` previously returned full details for *any* found row, including
expired, accepted, or revoked invitations — inconsistent with `accept_invitation`'s
`status != pending or expires_at < now` check, and with spec §4.4 (expired → 404).
`preview_invitation` (`app/services/onboarding/invites.py`) now applies the same check and
raises the same `NotFound()` (404) as an unknown token, so the response never leaks whether
a token existed but expired vs. was never issued. Tests:
`test_preview_expired_token_404`, `test_preview_accepted_token_404` (happy path unchanged:
`test_preview_returns_invite_details`).

**Verification:** full unit suite 148 passed (was 143 + 5 new); `make lint` clean; `make
e2e` 19 passed, unaffected (the e2e journey completes onboarding once and never re-touches
the mutating endpoints or previews an expired/accepted invite afterward).
