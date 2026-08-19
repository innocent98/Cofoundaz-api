# Design — Module 01 Onboarding (Plan 3 of 3)

> **Status:** Approved (brainstorm) · **Date:** 2026-08-15 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 01.6, §2.2), the
> foundation/auth spec `docs/superpowers/specs/2026-08-12-auth-onboarding-foundation-design.md`,
> and the live FE contract in `../cofoundaz/`.
>
> This is the design spec for the final slice of Module 01. It precedes the
> implementation plan (writing-plans) and the post-ship SOP (`docs/sop/`).
> Foundation (Plan 1) and Auth (Plan 2) are merged to `main`.

---

## 1. Scope

**In scope** — the 6-step resumable onboarding wizard with autosave, optional logo
upload, team invites, email-bound invitation acceptance, and
`POST /onboarding/complete` enqueuing the `roadmap.generate` +
`healthscore.initialize` stub jobs.

**Deferred (unchanged from the foundation spec):**

| Deferred | Seam used now | Built in |
|---|---|---|
| Onboarding AI assistant panel (streaming follow-ups) | `AIPanel` stub (canned) — not wired into the wizard this plan | Module 03 |
| Real notifications delivery | `event_bus` emission (`notification.*`) | Module 20 |
| Kickoff Assessment (Step 6 real flow) | `assessment_pending` flag + "do it later" path | Module 07 |
| Roadmap / Health Score generation | `JobDispatcher` enqueues `queued` rows | Modules 05 / 06 |
| Async worker draining jobs | `jobs` table rows only | Modules 05 / 06 |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Invitation acceptance | **Email-bound** — the accepting authenticated user's email MUST equal the invited email, else `403 INVITE_EMAIL_MISMATCH`. The invite is tied to the person it named. |
| 2 | `complete` gate | **Steps 1–4 required** (founder full name, startup name, industry, stage, ≥1 goal); invites (5) optional; **assessment (6) deferred** — complete always fires the jobs and sets `assessment_pending=True` ("do it later" → limited mode). |
| 3 | Logo upload | **Build now** via the existing `Storage` (local FS) seam → `startups.logo_url`. |
| 4 | Draft workspace resolution | The user's own onboarding workspace = the `startups` row with `created_by = user` AND `onboarding_completed_at IS NULL`. **Lazily created** (with a `founder` membership) on first `GET /onboarding/state`. No `X-Workspace-Id` header pre-completion. |
| 5 | Invite token | Opaque random token, stored **SHA-256-hashed** (reuse `hash_token` from `app/services/auth/sessions.py`), **14-day** TTL. |
| 6 | Idempotency | **Natural guards, no generic header.** `complete` is idempotent via the `onboarding_completed_at` timestamp (re-calls return the same result without re-enqueuing); `invites` dedupes by email. A generic `Idempotency-Key` middleware is deferred to Module 24 (Billing), where truly-billable POSTs need it — consistent with the auth plan's deferral. |

## 3. Data model (migration `0003_onboarding`)

**New table `invitations`** (UUIDMixin + TimestampMixin):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | `ondelete=CASCADE`, indexed |
| `email` | CITEXT | invited address, indexed |
| `role` | `MembershipRole` (Enum, native_enum=False) | the seat being offered |
| `token_hash` | String(64) | SHA-256 of the opaque invite token, **unique** |
| `status` | `InvitationStatus` (new enum) | `pending \| accepted \| expired \| revoked`, default `pending` |
| `invited_by` | UUID FK→users | the founder/inviter |
| `expires_at` | timestamptz | now + 14 days |
| `accepted_at` | timestamptz? | set on accept |
| `accepted_user_id` | UUID FK→users? | who accepted |

**New enum** `InvitationStatus(pending|accepted|expired|revoked)` in `app/db/models/enums.py`.

**Alter `startup_profiles`** — add `assessment_pending: bool` (default `True`, not null). Every new workspace needs the kickoff assessment until Module 07 flips it false; drives the FE limited-mode banner.

*Already built (no change):* `startups` (name/description/website/logo_url/industry/business_model/stage/country/created_by), `startup_profiles` (goals[]/notes/onboarding_step/onboarding_completed_at), `memberships`, `user_profiles`, `jobs`, `audit_log`.

## 4. Endpoints & flows

All under `/api/v1`. All require an authenticated, **email-verified** user (reuse
`get_current_user`; add an email-verified guard). Onboarding endpoints operate on
the caller's own draft workspace (Decision #4) — not a header-supplied one.

### 4.1 Onboarding wizard — `/onboarding`

- **`GET /onboarding/state`** → resolve or lazily create the draft workspace + a `founder` membership (emit `workspace.created` on first creation); return:
  ```json
  { "data": { "step": 1, "completed": false, "assessment_pending": true,
              "founder_profile": { "full_name": null, "role_title": "Founder & CEO", "country": null, "phone": null, "how_heard": null },
              "startup": { "id": "…", "name": null, "description": null, "website": null, "logo_url": null, "industry": null, "business_model": null, "stage": null },
              "goals": [], "notes": null,
              "invites": [ { "email": "…", "role": "team_member", "status": "pending" } ] } }
  ```
  If the user's workspace is already complete, return it with `completed: true` (FE redirects to dashboard).

- **`PATCH /onboarding/state`** → autosave one step. Body `{ "step": <1..4>, ...fields }`. Field mapping:
  - **1 — Founder Profile:** `full_name`, `role_title`, `country`, `phone?`, `how_heard?` → `user_profiles`.
  - **2 — Startup Profile:** `name`, `description?`, `website?` → `startups`.
  - **3 — Industry & Stage:** `industry`, `business_model` (enum), `stage` (enum) → `startups`.
  - **4 — Goals & Priorities:** `goals` (list, **max 3**, from the taxonomy), `notes?` → `startup_profiles`.
  Advances `onboarding_step = max(current, step)`. Validation errors use the standard `VALIDATION_ERROR` envelope. Returns the updated state (same shape as `GET`).

- **`POST /onboarding/logo`** (multipart `file`) → validate content-type (png/jpg/svg/webp) and size (**≤ 2 MB**, else `422 VALIDATION_ERROR`) → `Storage.save("logos/{startup_id}/{uuid}.{ext}", …)` → set `startups.logo_url` → `{ "data": { "logo_url": "…" } }`.

### 4.2 Team invites — `/onboarding/invites`

- **`POST /onboarding/invites`** (founder only) → `{ "invites": [ { "email", "role" } ] }`. For each: skip if the email is already an active member or has a pending invite (report `{skipped:[…]}`); else create an `invitations` row (hashed 14-day token) and send the invite email (`EmailSender`); emit `workspace.member.invited`. Dedupe-by-email makes re-submits safe. Returns `{ created:[…], skipped:[…] }`.

### 4.3 Complete — `/onboarding/complete`

- **`POST /onboarding/complete`** → **gate:** founder `full_name`, startup `name`, `industry`, `stage`, and ≥1 `goal` must be present, else `422 ONBOARDING_INCOMPLETE` with `field_errors` naming what's missing. On pass: set `onboarding_completed_at = now`, `assessment_pending = True`; enqueue `roadmap.generate` and `healthscore.initialize` (`JobDispatcher.enqueue(db, type, {"startup_id": …}, startup_id)`); emit `onboarding.completed`; emit the completion notification event; commit. **Idempotent:** if already completed, return the same `{ job_ids?, assessment_pending, completed:true }` without re-enqueuing. Returns `{ "data": { "job_ids": [ "…", "…" ], "assessment_pending": true } }`.

### 4.4 Invitations — `/invitations`

- **`GET /invitations/{token}`** (**public**, no auth) → preview for the FE `/invite/{token}` page. Look up by `hash_token(token)`; if not found/expired → `404 NOT_FOUND`. Return `{ "data": { "startup_name", "role", "inviter_name", "email", "status" } }`. Never reveals more than the invite itself.
- **`POST /invitations/accept`** (authenticated) → `{ "token" }`. Validate: exists, `status == pending`, not expired (else `400 TOKEN_INVALID`); **accepting user's email must equal `invitation.email`** (else `403 INVITE_EMAIL_MISMATCH`). If the user is already an active member of that startup → mark invite `accepted` and return `200` (idempotent, `ALREADY_MEMBER` not raised on self). Else create an **active** `membership` (invited role, `invited_by`), set invite `accepted`/`accepted_at`/`accepted_user_id`; emit `workspace.member.joined`; commit. Returns `{ "data": { "startup_id", "role" } }`.

## 5. Jobs, events, notifications

- **Jobs** (via `JobDispatcher`, `queued` rows, drained in Modules 05/06): `roadmap.generate`, `healthscore.initialize` — payload `{ startup_id }`. Their `job_id`s are returned from `complete` and pollable at `GET /api/v1/jobs/{id}` (already shipped).
- **Events** (`event_bus.publish`): `workspace.created`, `workspace.member.invited`, `onboarding.completed`, `workspace.member.joined`.
- **Notifications** (stub — no Module 20 yet): completion emits `notification.onboarding_complete` on the event bus; the real in-app/email delivery lands with Module 20. The **invite email** is real (via `EmailSender`).
- **AI panel:** not wired this plan (Module 03).

## 6. Errors

Reuse the shipped `AppError` taxonomy; add:

| Code | HTTP | When |
|---|---|---|
| `ONBOARDING_INCOMPLETE` | 422 | `complete` called before steps 1–4 satisfied; `field_errors` list the gaps |
| `INVITE_EMAIL_MISMATCH` | 403 | accepting user's email ≠ invited email |
| `ALREADY_MEMBER` | 409 | inviting an already-active member (per-invite skip, not a hard fail) / accept edge |
| `EMAIL_NOT_VERIFIED` | 403 | onboarding endpoint hit by an unverified user |

Invalid/expired invite tokens reuse `TOKEN_INVALID` (400). Founder-only endpoints that a non-founder hits reuse `FORBIDDEN` (403).

## 7. Testing

- **TDD**, real Postgres + per-test rollback, factory helpers (add `create_invitation`).
- **Unit/integration** per endpoint: state resolve/lazy-create, per-step autosave + validation, logo upload (content-type/size), invite create + dedupe, complete gate (each missing field) + job enqueue + idempotency, invite preview, accept happy path + email-mismatch 403 + expired 400 + already-member idempotency.
- **Live E2E extension** (`e2e/`): a founder journey — signup→verify→login→walk steps 1–4→invite a teammate→(second client) signup+verify the invitee→accept→complete→assert two `queued` jobs exist and `/me` now shows the membership/active workspace.
- Tenancy: onboarding mutations only touch the caller's own draft workspace; a second user can't PATCH/complete someone else's.

## 8. Plan shape

One implementation plan (`writing-plans`), ~11 TDD tasks, executed subagent-driven
(fresh implementer + independent review + fix loop per task, then a whole-branch
review), same rhythm as Plans 1–2:

1. `InvitationStatus` enum + `invitations` model + `assessment_pending` column + factory
2. Alembic migration `0003_onboarding`
3. Email-verified dependency + draft-workspace resolver service
4. `GET /onboarding/state`
5. `PATCH /onboarding/state` (per-step autosave + validation)
6. `POST /onboarding/logo` (Storage seam)
7. `POST /onboarding/invites` (+ dedupe + email)
8. `POST /onboarding/complete` (gate + job dispatch + completed-at idempotency)
9. `GET /invitations/{token}` (public preview)
10. `POST /invitations/accept` (email-bound)
11. Live E2E onboarding extension + SOP
