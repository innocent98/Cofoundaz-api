# SOP — Module 10 Marketing Hub, Slice 1: Content Calendar + Channels + Overview

**What shipped** — the CRUD spine of the Marketing Hub (Module 10, Slice 1 of 5): a Content
Calendar (create/list/get/update/delete marketing posts, with a manual publish transition), a
fixed 8-key Channels status board (lazy-seeded per workspace), and a minimal Overview stat strip
with three fields explicitly deferred to later slices. Founder/team_member access only. Publishing
an entry emits a `marketing.post.published` domain event and fans out an in-app notification to
the rest of the workspace.

Commits (branch `feat/module-10-marketing-slice1`, off `develop`; not yet merged, no PR opened
yet), oldest to newest:
`ef2e2d8` (design spec) → `1eb98f2` (implementation plan) →
`545f7f2` (`content_calendar` + `marketing_channels` tables + enums, migration `0033`) →
`bb0bd8c` (calendar service: CRUD, publish transition, `marketing.post.published` event) →
`2de5b92` (test fix: pin `scheduled`/`scheduled_at` rule on `update_entry`'s merged state) →
`eaea056` (channel lazy-seed + update + overview stats) →
`f7f37d8` (`/marketing` endpoints: calendar CRUD, channels, overview + RBAC) →
`1c80bb1` (in-app notification on `marketing.post.published`) →
`eef03d8` (e2e journey + captures) → this docs commit.

## Why

Module 10 (Marketing Hub) is the largest remaining unstarted PRD module — 8 sub-features across
5 entities (Content Calendar, Channels, Campaigns, AI Content Assistant, SEO Tools, Performance
Analytics). Following the same slice discipline used for Modules 03/08/18/20, the plain CRUD
spine ships first: a working calendar and channel board the FE can build against immediately,
establishing the `marketing` service namespace, the two core tables, the access model, and the
publish→notify event wiring that the AI layer (Slice 3) and analytics (Slice 5) will build on top
of rather than duplicate.

## How

**Mirrors the Journal/Dashboard CRUD shape rather than inventing a new one.** Endpoints are thin
(`app/api/v1/endpoints/marketing.py`) — parse, call `require_role`-gated service functions
(`app/services/marketing/service.py`), `db.commit()`, return `success_response(...)`. Services
`db.flush()` only; only the endpoint layer owns the transaction, same convention as every other
module in this API.

**Single channel per entry, not multi-channel.** `ContentCalendarEntry.channel` is one
`ChannelKey` value, matching the PRD's `content_calendar(...channel...)` column shape and keeping
the model simple for Slice 1. Multi-channel entries are an explicit deferred follow-up (see
below), not a partial implementation — there's no array/junction table lying around half-built.

**Channels are a fixed, closed taxonomy, not user-defined CRUD.** `ChannelKey` is an 8-value
`StrEnum` (`organic_social`, `paid_social`, `search`, `email`, `content_seo`, `partnerships`,
`events`, `referral`). `MarketingChannel` rows are **lazy-seeded**: `list_channels` queries the
existing rows for a startup, inserts whichever of the 8 keys are missing inside a
`db.begin_nested()` SAVEPOINT, and tolerates `IntegrityError` on the insert (a concurrent
first-read racing the same seed) by re-selecting the now-committed rows — the exact same
race-guard shape as `get_or_create_enrollment` and the Module 03 AI upgrades'
`get_or_create_recommendation_reason`/`get_or_create_today_prompt` (see
`docs/sop/2026-09-23-deferred-ai-upgrades.md`). **`PATCH /marketing/channels/{key}` does not
itself seed** — it only updates a row that already exists, and 404s otherwise. This is a
deliberate simplicity tradeoff made in the design spec (keep the write path a plain lookup,
push the seed responsibility onto `GET`), not an oversight; it does mean the FE must call `GET
/marketing/channels` once before the first `PATCH` for a workspace — flagged prominently in the FE
guide and tracked below as a documented sharp edge rather than a bug to fix in this slice.

**Publish is a status transition (`PATCH .../{id}` with `status: "published"`), not a separate
endpoint, and it is idempotent by construction.** `update_entry` computes
`just_published = new_status == ContentStatus.published and entry.published_at is None` — the
event only fires and `published_at` is only set on the **first** transition into `published`; a
repeat `PATCH {"status": "published"}` against an already-published entry is a safe no-op (200,
unchanged `published_at`, no second event, no duplicate notification). Verified by
`tests/services/marketing/test_calendar_service.py::test_publish_sets_published_at_and_emits_event_once`,
which asserts both the first-publish side effects and the no-op repeat in the same test.

**`status: "scheduled"` requires `scheduled_at`, enforced identically on create and update.**
Both `create_entry` and `update_entry` raise `VALIDATION_ERROR` (422) with a field error on
`scheduled_at` when the *merged* status would be `scheduled` but the *merged* `scheduled_at` is
still `None` — on `update_entry` this reads the incoming payload's `scheduled_at` if present,
else falls back to the entry's existing value, so a `PATCH {"status": "scheduled"}` against an
entry that already has a `scheduled_at` succeeds without re-sending the time. A dedicated test
(`test_update_to_scheduled_without_time_is_422`) was added during Task 2's review round after the
reviewer flagged this branch as correct-but-untested.

**Publish notification uses the existing Module 20 event→registry pattern, not new
infrastructure.** `_emit_published` calls `event_bus.publish(db, "marketing.post.published",
{...})`; a new `NotifSpec` entry in `app/services/notifications/registry.py` maps that event to
`_members_minus_actor` (recipients = every active membership in the workspace except whoever
published) with the title `f"Scheduled post published: {title}"` and an empty body. No email
channel is wired for this event in Slice 1 — in-app only, matching the design spec's "wired now"
decision for the notification while explicitly deferring auto-publish-at-`scheduled_at` (a
scheduler-driven feature, not this slice's concern).

**Access is `require_role(founder, team_member)`, with no per-entry ownership scoping.** Every
founder and team member in a workspace can read/write the whole calendar and channel board — there
is no "my posts only" filter and no granular "Marketing grant" (the design spec explicitly
deferred that: no per-module grant infrastructure exists anywhere in this codebase yet, and
building it is a cross-cutting effort that belongs to its own slice, not this one).

**Overview ships the two fields Slice 1 can actually compute, with the other three as explicit
`null` constants — not omitted, not zeroed.** `scheduled_this_week` counts `status="scheduled"`
entries whose `scheduled_at` falls in the current ISO week (`_week_bounds`, Monday 00:00 UTC
inclusive → next Monday exclusive); `active_channels` counts `marketing_channels` rows with
`status="active"`. `active_campaigns`, `top_channel_by_conversions`, and `ai_content_ideas` are
hardcoded `None` with an inline comment naming which slice fills each (S2, S5, S3 respectively) —
same "ship the shape now, fill it in later without an endpoint/schema change" pattern
`OverviewResponse` already establishes for every other stat-strip endpoint in this API.

## What's involved

**Migration `0033_marketing_calendar_channels`** (chains off `0032_journal_prompts`, sole alembic
head) — two new tables:
- `content_calendar`: `id` (uuid PK), `startup_id` (uuid FK → `startups.id`, `ondelete="CASCADE"`,
  indexed), `created_by` (uuid FK → `users.id`, `ondelete="SET NULL"`, nullable, indexed), `title`
  (`String(200)`, not null), `channel` (non-native enum, 8 `ChannelKey` values, length 20, not
  null), `status` (non-native enum, `draft`/`scheduled`/`published`, length 12, not null), `body`
  (`Text`, nullable), `media_ref` (`String(500)`, nullable — an opaque id/URL slot for Module 18
  media; not fetched or validated server-side in this slice), `scheduled_at` / `published_at`
  (`DateTime(timezone=True)`, both nullable), `created_at`/`updated_at` (timestamptz, server
  default `now()`).
- `marketing_channels`: `id` (uuid PK), `startup_id` (uuid FK CASCADE, indexed), `key`
  (non-native enum, same 8 `ChannelKey` values, length 20, not null), `status` (non-native enum,
  `active`/`testing`/`paused`/`not_started`, length 12, not null, default `not_started`), `notes`
  (`Text`, nullable), `created_at`/`updated_at`; unique constraint
  `uq_marketing_channel_startup_key` on `(startup_id, key)`.

**Enums** — `app/db/models/enums.py`: `ContentStatus`, `ChannelStatus`, `ChannelKey` (all
`enum.StrEnum`, mapped via `Enum(Cls, native_enum=False, length=N)` per this project's convention).

**Models** — `app/db/models/marketing.py` (new): `ContentCalendarEntry`, `MarketingChannel`
(both `UUIDMixin, TimestampMixin, Base`); registered in `app/db/models/__init__.py` for
`create_all`-built test DBs and Alembic autogenerate.

**Schemas** — `app/schemas/marketing.py` (new): `CalendarEntryCreate`, `CalendarEntryUpdate`,
`CalendarEntryResponse`, `ChannelUpdate`, `ChannelResponse`, `OverviewResponse`.

**Service** — `app/services/marketing/service.py` (new): `create_entry`, `get_entry`,
`list_entries` (date-range + channel + status filters), `update_entry`, `delete_entry`,
`serialize_entry`, `_emit_published`, `list_channels` (lazy-seed), `update_channel`, `overview`,
`_week_bounds`.

**Endpoints** — `app/api/v1/endpoints/marketing.py` (new), mounted at `/marketing`
(`app/api/v1/api.py`): `GET /marketing`, `POST /marketing/calendar-entries`,
`GET /marketing/calendar-entries`, `GET /marketing/calendar-entries/{id}`,
`PATCH /marketing/calendar-entries/{id}`, `DELETE /marketing/calendar-entries/{id}`,
`GET /marketing/channels`, `PATCH /marketing/channels/{key}` — all behind
`require_role(MembershipRole.founder, MembershipRole.team_member)`.

**Notification wiring** — `app/services/notifications/registry.py`: new `NotifSpec` entry for
`"marketing.post.published"` (`_members_minus_actor`, title lambda, empty body lambda).

**Tests**
- `tests/db/test_marketing_models.py` (2) + `tests/test_marketing_migration.py` (2) — round-trip,
  unique constraint, single alembic head.
- `tests/services/marketing/test_calendar_service.py` (8) — create/get, cross-tenant 404,
  scheduled-without-time 422 on create and on update, date-range/channel filtering, publish
  sets-once + no-repeat-fire, delete.
- `tests/services/marketing/test_channel_service.py` (4) — lazy-seed idempotency, update, missing
  key 404, overview counts.
- `tests/api/test_marketing.py` (49 parametrized cases) — RBAC matrix (403 for
  mentor/accountant/legal_advisor/business_consultant/investor, 200 for founder/team_member, 401
  unauthenticated) across every route, full calendar-entry CRUD lifecycle via the API, scheduled
  422, channel seed+update, overview shape.
- `tests/services/notifications/test_marketing_notification.py` (2) — publish notifies workspace
  minus actor with the exact title string; a non-publish update creates no notification.
- `e2e/test_marketing.py::test_marketing_journey` (new) — full live journey against the running
  app: onboard → overview (before) → channels (8 seeded) → activate `email` channel → create a
  scheduled entry → list by date range → publish → overview (after, `active_channels: 1`).
  Captures every response body to `e2e/_captures/marketing/`.

**Errors / API surface — all new** (this is a new module): `VALIDATION_ERROR` (422) on blank
title or `scheduled` status missing `scheduled_at`; `FORBIDDEN` (403) for non-marketing roles;
`NOT_FOUND` (404) for missing/cross-tenant entries and for `PATCH /channels/{key}` before that
workspace's first `GET /channels`.

**Docs**
- `docs/fe-integration-guide-marketing-calendar.md` (new) — every endpoint, real captured bodies,
  enums, the publish-idempotency behavior, the `scheduled_at` 422 rule, the channel-seed-before-
  patch sharp edge, the Overview deferred-null fields.
- `docs/checklist/PROJECT_CHECKLIST.md` (reconciled — see below).

## Verification

**Per-task unit verification (green before the e2e task), oldest to newest:**
- Task 1 (models + migration): `tests/db/test_marketing_models.py` + `tests/test_marketing_migration.py`
  — 4/4 passed. Full suite: **1383 passed**, 1 pre-existing warning.
- Task 2 (calendar service): 6 passed on the focused file → full suite **1389 passed** → after
  the review-flagged `scheduled_at` test addition, 8 passed on the focused file → full suite
  **1395 passed** (folded into Task 3's number below; see Task 3's own count for the
  post-fix state).
- Task 3 (channel lazy-seed + update + overview): `tests/services/marketing/` — 12 passed (4 new
  channel/overview tests). Full suite: **1395 passed** in 144.41s.
- Task 4 (endpoints + RBAC): `tests/api/test_marketing.py` — 49 passed. Full suite:
  **1444 passed**, exit code 0, no regressions.
- Task 5 (notification wiring): `tests/services/notifications/test_marketing_notification.py` —
  2 passed; `tests/services/notifications/` — 25 passed (no regressions). Full suite:
  **1446 passed**, 0 failures, 127.55s.
- `poetry run ruff check` / `black --check` clean on every touched file, each task.
- `alembic heads` — single linear head (`0033_marketing_calendar_channels`) confirmed after the
  migration task; no drift between the ORM models and the applied migration.

**Live e2e (`bash scripts/e2e_run.sh`, full suite):**

```
53 passed in 40.73s
```

All 53 e2e journeys pass (52 pre-existing + `test_marketing.py::test_marketing_journey`, no other
file changed), confirming no regression. The journey: sign up + verify + onboard a founder →
`GET /marketing` (captures `overview_before.json`) → `GET /marketing/channels` (asserts 8 rows,
captures `channels.json`) → `PATCH /marketing/channels/email` (`status: active, notes: "warming
up"`, captures `channel_updated.json`) → `POST /marketing/calendar-entries` (scheduled entry,
captures `entry_created.json`) → `GET /marketing/calendar-entries?from=...&to=...` (asserts the
entry present, captures `entries_list.json`) → `PATCH .../{id}` (`status: published`, asserts
`published_at` set, captures `entry_published.json`) → `GET /marketing` again (asserts
`active_channels == 1`, captures `overview_after.json`).

**Honest gap disclosure.** The e2e journey exercises the *first* publish only — the idempotent
no-repeat-fire behavior on a second `PATCH {"status": "published"}` against an already-published
entry is unit-tested (`test_publish_sets_published_at_and_emits_event_once`) but not separately
e2e-captured; same for the `PATCH /channels/{key}` 404-before-first-`GET` sharp edge (unit-tested
via `test_update_missing_channel_not_found`, not captured live, since the e2e journey's own
sequence always calls `GET /channels` first). Both gaps are called out explicitly in the FE
guide's verification table rather than silently presented as live-verified.

## Operate / roll back

**New deploy-time requirement: none.** No new background job, no new container, no new config.
The publish notification is created synchronously in the same request that flips `status` to
`published` (via the existing event bus + notifications registry, same as every other in-app
notification in this API) — nothing here depends on the worker process.

**Rollback:** revert this slice's commits as a unit (`ef2e2d8..eef03d8`, plus this docs commit)
and downgrade the migration (`poetry run alembic downgrade 0032_journal_prompts`) to drop
`content_calendar` and `marketing_channels`. Safe — neither table is read by any other feature.
As with every other slice's rollback note in this repo: downgrade the migration only *after* the
app code is already rolled back, not before (reverting the migration first, in front of
still-live code expecting the tables, would make the next `/marketing` call 500).

## Follow-ups

**Slices 2–5 of Module 10, all deferred by design, not gaps in this slice:**
- **Slice 2 — Campaigns + Audience Segments.** Fills `active_campaigns` on the Overview.
- **Slice 3 — AI layer** (`Write with AI` / `Plan my week` / copy generation). Fills
  `ai_content_ideas`; nothing in Slice 1 blocks it — the calendar CRUD this slice ships is exactly
  what those AI-generated entries will be created through.
- **Slice 4 — SEO tools.**
- **Slice 5 — Performance Analytics + Module 22 export.** Fills
  `top_channel_by_conversions`.

**Deferred within the CRUD spine itself:**
- **Auto-publish at `scheduled_at`.** Slice 1's status transitions are manual (`PATCH`) only; a
  scheduler-driven auto-publish hooking Module 20's cron infra is a real, separate follow-up.
- **Multi-channel entries.** `ContentCalendarEntry.channel` is a single `ChannelKey`; broadening
  to multiple channels per entry needs its own model change (array or junction table), not a
  mechanical extension.
- **Media upload integration.** `media_ref` is presently an untouched opaque string slot; wiring
  it to Module 18's actual upload/URL flow is unbuilt.
- **Granular per-module "Marketing grant."** Access is currently workspace-wide
  founder/team_member, with no per-entry ownership or narrower grant — building real grant
  infrastructure is a cross-cutting effort spanning more than this module.
- **`PATCH /marketing/channels/{key}` requires a prior `GET /marketing/channels` to seed the
  row for that workspace, or it 404s.** This is documented as the deliberate design tradeoff it
  is (see How), not flagged as a defect — but it is a genuine footgun for any FE code path that
  might `PATCH` a channel without having rendered the channels board first (a deep link, a
  keyboard shortcut, a bulk-update script). Called out prominently in the FE guide; a future pass
  could make `PATCH` auto-seed the missing row instead of 404ing, trading a small amount of extra
  write-path complexity for removing the ordering requirement entirely.
