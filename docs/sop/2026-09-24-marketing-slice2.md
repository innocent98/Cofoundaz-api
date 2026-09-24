# SOP — Module 10 Marketing Hub, Slice 2: Campaigns + Audience Segments

**What shipped** — the second slice of the Marketing Hub (Module 10, Slice 2 of 5): reusable
**Audience Segments** (name + rule-set container + estimated size + optional linked persona) and
**Campaigns** (objective, budget in cents, a per-channel percent mix, targeted segments), moved
through a guarded `draft → active → paused/completed` lifecycle. Launching and completing a
campaign emit `marketing.campaign.launched`/`marketing.campaign.completed` domain events and
fan out in-app notifications, on the same event/registry pattern Slice 1 established. Founder/
team_member access only, same RBAC dependency as Slice 1.

Commits (branch `feat/module-10-marketing-slice2`, off `feat/module-10-marketing-slice1`'s merge
point; not yet merged, no PR opened yet), oldest to newest:
`3a760ee` (design spec) → `3383279` (implementation plan) →
`9ff939c` (`campaigns` + `audience_segments` + `campaign_segments` tables + enums, migration
`0034_campaigns_segments`) → `8c546fa` (segments service: CRUD, persona-kind validation,
used-by) → `e401544` (campaigns service: CRUD, guarded lifecycle, `campaign.*` events) →
`79aae4d` (fix: validate the status transition before mutating any field, closing a partial-write
gap a reviewer caught) → `ab70ac7` (segment + campaign endpoints + RBAC) → `559119d` (in-app
notifications on launched/completed) → `76657e3` (e2e journey + 7 captures) → this docs commit.

## Why

Module 10 Slice 1 shipped the Content Calendar + Channels CRUD spine and left `Overview`'s
`active_campaigns` field as an explicit `null` constant naming Slice 2 as the filler. This slice
is that fill: the campaign-planning half of the Marketing Hub, following the same slice
discipline as Modules 03/08/18/20/Slice-1 itself — a working, testable CRUD + lifecycle spine the
FE can build against now, with the AI channel-plan recommender (Slice 3) and real performance
metrics (Slice 5) explicitly deferred rather than half-built.

## How

**Extends Slice 1's `marketing` namespace rather than starting a new one.** Two new service
modules, `app/services/marketing/campaigns.py` and `app/services/marketing/segments.py`, sit
alongside Slice 1's `app/services/marketing/service.py` (calendar + channels) and share its
`_validation()` helper (a small `AppError` factory producing the
`{"field": ..., "message": ...}` shape) rather than duplicating it. Endpoints stay thin — parse,
call a `require_role`-gated service function, `db.commit()`, `success_response(...)` — the same
convention Slice 1 and every other module in this API follows.

**`channel_mix` is `{ChannelKey: percent}`, not `{ChannelKey: cents}`.** A campaign's `budget` is
the single money field (integer cents); `channel_mix` values are `0–100` percentages the FE
multiplies against `budget` to derive per-channel spend client-side. The percentages are
deliberately **not** validated to sum to 100 — matching the design spec's UI-editable-sliders
model, where an in-progress edit can transiently not sum to 100 without the server rejecting it.
Validation (`_validate_channel_mix`, `app/schemas/marketing.py`) runs as a Pydantic
`field_validator` on both `CampaignCreate` and `CampaignUpdate`, checking each key against the
`ChannelKey` enum and each value against `0 ≤ pct ≤ 100` — a schema-boundary check, which is why
its 422 carries the generic `"Please check the highlighted fields."` top-level message rather
than the service layer's specific one (documented as a deliberate two-shapes-same-code nuance in
the FE guide).

**Guarded lifecycle via an explicit transition table, not a state-machine library.**
`app/services/marketing/campaigns.py::_TRANSITIONS` is a plain
`dict[tuple[CampaignStatus, CampaignStatus], str | None]` mapping the 4 legal moves
(`draft→active`, `active→completed`, `active↔paused`) to the event name to fire (or `None` for
the two pause/resume moves, which are silent). `update_campaign` pops `status` out of the
`exclude_unset` field dict, looks up `(current, new)` in the table, and raises `422` on any miss —
**before** touching any other field on the ORM object. Re-issuing the campaign's current status
(`current == new`) is treated as a no-op read-through, not a transition at all, so it skips the
table lookup entirely rather than 404ing on a "transition" that isn't a state change.

**A same-branch fix closed a partial-write gap a reviewer caught (`79aae4d`).** The first cut of
`update_campaign` applied every other field (`name`, `budget`, `channel_mix`, …) via the
`setattr` loop **before** checking whether the requested status transition was legal, so an
illegal transition (e.g. `draft → completed`) still landed `name`/`budget` changes from the same
`PATCH` body while rejecting only the status. The fix reordered the transition-legality check
ahead of the mutation loop, so an illegal transition now rejects the **entire** request with
nothing written — verified by a new
`test_illegal_transition_emits_no_event_and_leaves_timestamps`, which asserts the campaign's
status, `launched_at`, and `completed_at` are all unchanged after the rejected call. This makes a
rejected `PATCH` always safe to retry with a corrected body.

**Segments validate `persona_id` by tenancy *and* kind in one query.** `_validate_persona`
(`app/services/marketing/segments.py`) does a single
`filter_by(id=persona_id, startup_id=startup_id, kind=RecordKind.persona)` against
`BusinessRecord` (Module 08's Business Builder records) — a `persona_id` that's cross-tenant,
missing entirely, or exists-but-is-the-wrong-`RecordKind` (e.g. a `competitor` row) all fail the
same query and produce the identical 422, by design: the FE doesn't need to distinguish "wrong
kind" from "not yours" copy-wise.

**`campaign_segments` is a plain join table with server-side dedupe, not a rich model.**
`_set_segments` fully replaces a campaign's segment links on every write that touches
`segment_ids` (`DELETE` then re-`INSERT`, inside the same flush) — simpler than a diff/reconcile,
acceptable because segment counts per campaign are small. `dict.fromkeys(segment_ids)` dedupes
while preserving the caller's order, so `segment_ids` in the response reflects insertion order
(via each join row's own `created_at`), not alphabetical or id order.

**Access is the same `require_role(founder, team_member)` dependency instance Slice 1 already
built**, not a new one — segments and campaigns hang off the same `/marketing` router and the
same `_marketing` dependency object, so there was no new RBAC wiring to write, only new routes to
register behind it.

**Notification wiring reuses Slice 1's exact pattern, no new infrastructure.** Two new `NotifSpec`
rows in `app/services/notifications/registry.py` (`marketing.campaign.launched`,
`marketing.campaign.completed`), both `_members_minus_actor` recipients, both left unmapped in
`EVENT_CATEGORY` — in-app only, no email, matching Slice 1's `marketing.post.published` precedent
and the design spec's explicit deferral of an email channel for this slice.

## What's involved

**Migration `0034_campaigns_segments`** (chains off `0033_marketing_calendar_channels`, sole
alembic head) — three new tables:
- `audience_segments`: `id` (uuid PK), `startup_id` (uuid FK → `startups.id`, CASCADE, indexed),
  `name` (`String(200)`, not null), `definition` (JSONB, not null, default `{}`), `est_size`
  (nullable int), `persona_id` (uuid FK → `business_records.id`, `ondelete="SET NULL"`, nullable,
  indexed), `created_at`/`updated_at`.
- `campaigns`: `id` (uuid PK), `startup_id` (uuid FK CASCADE, indexed), `name` (`String(200)`),
  `objective` (non-native enum, 4 `CampaignObjective` values, length 12, not null), `budget`
  (`Integer`, not null, default `0`, `server_default="0"` — cents), `channel_mix` (JSONB, not
  null, default `{}`), `status` (non-native enum, 4 `CampaignStatus` values, length 12, not null,
  default `draft`), `metrics` (JSONB, not null, default `{}` — stays empty until Slice 5),
  `period_start`/`period_end` (`Date`, nullable), `launched_at`/`completed_at`
  (`DateTime(timezone=True)`, nullable), `created_at`/`updated_at`.
- `campaign_segments`: `id` (uuid PK), `campaign_id` (uuid FK → `campaigns.id`, CASCADE,
  indexed), `segment_id` (uuid FK → `audience_segments.id`, CASCADE, indexed), unique constraint
  `uq_campaign_segment` on `(campaign_id, segment_id)`, `created_at`/`updated_at`.

**Migration-id note:** the brief's originally-specified id,
`0034_marketing_campaigns_segments` (33 chars), exceeds Alembic's default
`alembic_version.version_num` `VARCHAR(32)` — `alembic upgrade head` failed with a real
`StringDataRightTruncation` the first time it was run (DDL rolled back cleanly, no partial
state). Renamed to **`0034_campaigns_segments`** (23 chars) for both the filename and the
`revision` string; `down_revision` still correctly points at `0033_marketing_calendar_channels`.
Every doc in this repo now cites `0034_campaigns_segments`, not the brief's original longer id.

**Enums** — `app/db/models/enums.py`: `CampaignObjective` (`awareness`/`leads`/`sales`/`launch`),
`CampaignStatus` (`draft`/`active`/`paused`/`completed`), both `enum.StrEnum` mapped via
`Enum(Cls, native_enum=False, length=N)`, same convention as every other enum in this repo.

**Models** — `app/db/models/marketing.py` (extended, Slice 1's `ContentCalendarEntry`/
`MarketingChannel` untouched): `AudienceSegment`, `Campaign`, `CampaignSegment` (all
`UUIDMixin, TimestampMixin, Base`); registered in `app/db/models/__init__.py`.

**Schemas** — `app/schemas/marketing.py` (extended): `SegmentCreate`, `SegmentUpdate`,
`SegmentResponse`, `CampaignCreate`, `CampaignUpdate`, `CampaignResponse`, plus the shared
`_validate_channel_mix` helper both campaign schemas' `field_validator`s call.

**Services** —
`app/services/marketing/segments.py` (new): `create_segment`, `get_segment`, `list_segments`,
`update_segment`, `delete_segment`, `segment_campaigns` (used-by), `serialize_segment`,
`_validate_persona`.
`app/services/marketing/campaigns.py` (new): `_TRANSITIONS`, `create_campaign`, `get_campaign`,
`list_campaigns`, `update_campaign`, `delete_campaign`, `serialize_campaign`,
`campaign_segment_ids`, `_validate_segment_ids`, `_set_segments`. Both import `_validation` from
Slice 1's `app/services/marketing/service.py` rather than redefining it.

**Endpoints** — `app/api/v1/endpoints/marketing.py` (extended, Slice 1's 8 routes untouched): 6
new segment routes (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}` + `GET
{id}/campaigns`) and 5 new campaign routes (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`),
all behind the same `_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)`
instance Slice 1 already defined.

**Notification wiring** — `app/services/notifications/registry.py`: two new `NotifSpec` entries,
`marketing.campaign.launched` and `marketing.campaign.completed`, both `_members_minus_actor` +
a title lambda + an empty body lambda. `app/services/notifications/categories.py` deliberately
left untouched — both events stay unmapped in `EVENT_CATEGORY`, so delivery stays in-app-only.

**Tests**
- `tests/db/test_marketing_models.py` + `tests/test_marketing_migration.py` — extended for the 3
  new tables, single-head assertion.
- `tests/services/marketing/test_segments_service.py` (7) — create/get, persona-kind validation,
  cross-tenant persona rejection, cross-tenant 404, used-by, delete.
- `tests/services/marketing/test_campaigns_service.py` (9) — create with segments + channel mix,
  foreign segment_id 422, bad channel_mix 422 (schema-level), launch sets timestamp + emits once,
  illegal transition 422, illegal transition leaves no partial write (review-driven addition),
  re-issuing current status is a silent no-op, full lifecycle, cross-tenant 404.
- `tests/api/test_marketing_campaigns.py` (5) — full segment+campaign CRUD + used-by over HTTP,
  illegal-transition 422, bad-channel-mix 422, RBAC-forbidden for mentor/investor.
- `tests/services/notifications/test_marketing_campaign_notification.py` (2) — launch and
  completion each notify the workspace minus the actor, with the exact title strings.
- `e2e/test_marketing.py::test_marketing_campaigns_journey` (new, alongside Slice 1's
  `test_marketing_journey` in the same file) — segment create → campaign create → list → launch
  → pause → resume → complete → segment used-by, 7 live captures under
  `e2e/_captures/marketing/`.

**Errors / API surface — additive only** (no existing Slice 1 route/shape changed):
`VALIDATION_ERROR` (422) on blank `name`, bad `channel_mix` (schema-level), foreign `segment_ids`
(service-level), non-persona/cross-tenant `persona_id` (service-level), illegal campaign status
transition (service-level); `FORBIDDEN` (403) for non-marketing roles (same dependency as Slice
1); `NOT_FOUND` (404) for missing/cross-tenant segments and campaigns.

**Docs**
- `docs/fe-integration-guide-marketing-campaigns.md` (new) — every segment + campaign endpoint,
  real captured bodies where the e2e journey exercises them, the `channel_mix` percent/cents
  shape and its float round-trip trap, the full guarded-transition table, every 422 (with the
  two-shapes-same-code nuance called out), the used-by endpoint, `metrics` staying `{}` until
  Slice 5. Cross-references `docs/fe-integration-guide-marketing-calendar.md`.
- `docs/checklist/PROJECT_CHECKLIST.md` (reconciled — see below).

## Verification

**Per-task unit verification (green before the e2e task), oldest to newest:**
- Task 1 (enums + models + migration): model/migration round-trip tests passed; single alembic
  head `0034_campaigns_segments` confirmed.
- Task 2 (segments service): `tests/services/marketing/test_segments_service.py` — 7 passed.
- Task 3 (campaigns service): `tests/services/marketing/test_campaigns_service.py` — 9 passed
  (7 original + 2 added during the review-driven `79aae4d` fix). Full suite: **1465 passed**.
- Task 4 (endpoints + RBAC): `tests/api/test_marketing_campaigns.py` +
  `tests/api/test_marketing.py` (Slice 1's untouched RBAC/CRUD suite) — 54 passed together, no
  regression on the pre-existing 49. Full suite: **1470 passed**, 97% coverage.
- Task 5 (notification wiring): `tests/services/notifications/test_marketing_campaign_notification.py`
  — 2 passed; `tests/services/notifications/` — 27 passed (no regressions). Full suite:
  **1472 passed**, 0 failed. `mypy app` clean (178 source files); `pylint app --fail-under=9.5` —
  9.90/10, no new findings.
- `poetry run black`/`isort`/`ruff check` clean on every touched file, each task.
- `alembic heads` — single linear head (`0034_campaigns_segments`) confirmed after the migration
  task; no drift between the ORM models and the applied migration.

**Live e2e (`bash scripts/e2e_run.sh`, full suite):**

```
e2e/test_marketing.py::test_marketing_journey PASSED
e2e/test_marketing.py::test_marketing_campaigns_journey PASSED
...
54 passed in 37.54s
```

All 54 e2e journeys pass (53 pre-existing + `test_marketing_campaigns_journey`, no other file
changed), confirming no regression. The new journey: onboard a founder → create a segment
(captures `segment_created.json`) → create a campaign targeting it (captures
`campaign_created.json`) → list campaigns (captures `campaigns_list.json`) → launch
(`draft→active`, asserts `launched_at` set, captures `campaign_launched.json`) → pause
(`active→paused`, captures `campaign_paused.json`) → resume (`paused→active`, asserted but not
separately captured) → complete (`active→completed`, asserts `completed_at` set, captures
`campaign_completed.json`) → segment used-by (asserts the campaign appears, captures
`segment_used_by.json`).

**Honest gap disclosure.** The e2e journey exercises only the 4 legal transitions it needs for
the happy path (`draft→active→paused→active→completed`); every illegal-transition 422, both
`channel_mix` 422 variants, the `segment_ids` 422, both `persona_id` 422 variants, segment
list/get/update/delete, and campaign get/delete are unit- or integration-tested but not
e2e-captured. All are called out explicitly in the FE guide's verification table rather than
silently presented as live-verified.

## Operate / roll back

**New deploy-time requirement: none.** No new background job, no new container, no new config.
Both campaign notifications are created synchronously in the same request that transitions
`status` (via the existing event bus + notifications registry) — nothing here depends on the
worker process.

**Rollback:** revert this slice's commits as a unit (`3a760ee..76657e3`, plus this docs commit)
and downgrade the migration (`poetry run alembic downgrade 0033_marketing_calendar_channels`) to
drop `campaign_segments`, `campaigns`, and `audience_segments`. Safe — no other feature reads
these tables yet. As with Slice 1's rollback note: downgrade the migration only *after* the app
code is already rolled back, not before.

## Follow-ups

**Slices 3–5 of Module 10, all deferred by design, not gaps in this slice:**
- **Slice 3 — AI Content Assistant**, including an AI channel-plan recommender for `channel_mix`
  (fills `ai_content_ideas` on the Overview; nothing in this slice blocks it).
- **Slice 4 — SEO Tools.**
- **Slice 5 — Performance Analytics.** Fills `metrics` (currently always `{}`) and
  `top_channel_by_conversions`; will likely add a "campaign summary ready" notification.

**Deferred within Slice 2 itself:**
- **Segment rule execution.** `AudienceSegment.definition` is stored and returned as an opaque
  JSON blob today — there is no engine that evaluates its rules against real user/customer data,
  and `est_size` is a manually-entered number, not computed from the definition.
- **Campaign Content step.** No attach/generate-assets flow and no link from a campaign to
  specific Slice 1 calendar entries yet — the PRD's "Content" step of campaign planning is
  unbuilt.
- **No `cancelled` terminal status.** Only `draft`/`active`/`paused`/`completed` exist;
  `completed` is the only terminal state, and there's no server-side "abandon this campaign"
  transition distinct from delete. Add a `cancelled` status if the FE needs to keep a killed
  campaign's history instead of hard-deleting it.
- **Multi-channel calendar entries** (carried over from Slice 1, unrelated to this slice's own
  scope) — still a single `ChannelKey` per `ContentCalendarEntry`.
- **Granular per-module "Marketing grant"** (carried over from Slice 1) — access is still
  workspace-wide founder/team_member, no per-entry ownership or narrower grant.
