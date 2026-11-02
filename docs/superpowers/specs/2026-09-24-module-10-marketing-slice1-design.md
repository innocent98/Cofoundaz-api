# Module 10 — Marketing Hub, Slice 1: Content Calendar + Channels core (design)

**Status:** approved-for-planning
**Date:** 2026-09-24
**Module:** 10 (Marketing Hub) — Slice 1 of 5. Backend API only (this repo is `cofoundaz-api`; the FE consumes these endpoints).
**Depends on:** the Foundation/Tenancy spine (`require_workspace`, `require_role`), the event bus + notifications registry (Module 20), and the job worker (only referenced by later slices). Nothing here is blocked.

## Goal

Ship the CRUD spine of the Marketing Hub: a **Content Calendar** (plan/schedule/publish marketing posts) and a per-startup **Channels** status board over a fixed channel taxonomy, plus a minimal **Overview** stat strip. This is the foundation the later slices build on (Campaigns/Segments → S2, the AI layer → S3, SEO → S4, Analytics → S5).

## Why

Module 10 is large (8 sub-features, 5 entities). Following the project's slice discipline (Modules 08/18/20), the CRUD spine ships first, the AI layer is seamed-and-deferred to Slice 3, and analytics comes last. Slice 1 gives the FE a working calendar + channels board immediately, and establishes the `marketing-service` namespace, entities, events, and access model the rest of the module extends.

## Decisions settled in brainstorming

| Decision | Choice | Consequence |
| --- | --- | --- |
| Channel per calendar entry | **Single channel** (a `ChannelKey` enum value) | Matches the PRD entity `content_calendar(...channel...)`; simplest model. Multi-channel is a follow-up. |
| Channels model | **Fixed 8-key taxonomy, lazy-seeded per-startup status rows** | A `ChannelKey` enum; `GET /marketing/channels` get-or-creates the 8 rows for the startup; each editable (status + notes). Not arbitrary user CRUD. |
| Access control | **`require_role(founder, team_member)`; granular "Marketing grant" deferred** | No per-module grant infra exists in the codebase; building it is a cross-cutting effort for its own slice. |
| AI features | **Fully deferred to Slice 3** | No `Write with AI` / `Plan my week` endpoints in S1; the calendar CRUD supports the entries they will later create. |
| Overview stat strip | **Ship minimal now**, deferred stats returned as explicit `null`/`0` | FE renders the strip progressively as later slices land. |
| Published notification | **Wired now** via the notifications registry | The event→subscriber infra is ready; recipients = the startup's founders + the entry's creator. |
| Auto-publish at `scheduled_at` | **Deferred** (follow-up) | S1 status transitions are manual (a `PATCH`); a scheduler-driven auto-publish can hook Module 20's cron later. |

## Scope

### In scope
1. Enums: `ContentStatus`, `ChannelStatus`, `ChannelKey` (`app/db/models/enums.py`).
2. Models + migration `0033`: `ContentCalendarEntry` (table `content_calendar`), `MarketingChannel` (table `marketing_channels`).
3. Pydantic schemas (`app/schemas/marketing.py`).
4. Service layer (`app/services/marketing/service.py`): calendar CRUD, channel lazy-seed + update, overview stats, publish transition + event emit.
5. Endpoints (`app/api/v1/endpoints/marketing.py`), mounted at `/marketing`.
6. Domain event `marketing.post.published` + a notifications-registry subscriber ("Scheduled post published: {title}").
7. Tests (unit + e2e journey), FE integration guide, SOP, checklist entry.

### Out of scope (later slices / deferred)
- Campaigns, Audience Segments (S2); AI copy/plan/recommend/fit-notes/readout (S3); SEO tools (S4); Performance Analytics + Module 22 export (S5).
- Multi-channel entries; auto-publish scheduler; media upload (only a `media_ref` string slot — actual upload is Module 18's concern, referenced by URL/id); granular per-module grants.

## Architecture

### Enums — `app/db/models/enums.py`
```python
class ContentStatus(enum.StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    published = "published"

class ChannelStatus(enum.StrEnum):
    active = "active"
    testing = "testing"
    paused = "paused"
    not_started = "not_started"

class ChannelKey(enum.StrEnum):
    organic_social = "organic_social"
    paid_social = "paid_social"
    search = "search"
    email = "email"
    content_seo = "content_seo"
    partnerships = "partnerships"
    events = "events"
    referral = "referral"
```
Mapped via `Enum(Cls, native_enum=False, length=N)` per project convention.

### Data model — `app/db/models/marketing.py` (new) + migration `0033`

`ContentCalendarEntry(UUIDMixin, TimestampMixin, Base)` → `content_calendar`:
- `startup_id` FK `startups.id` CASCADE, `index=True`.
- `created_by` FK `users.id` (no cascade delete of the entry when a user is removed — `ondelete="SET NULL"`, nullable), `index=True`.
- `title: str` `String(200)`, non-null.
- `channel: ChannelKey` `Enum(ChannelKey, native_enum=False, length=20)`, non-null.
- `status: ContentStatus` `Enum(..., length=12)`, non-null, `default=draft`.
- `body: str | None` `Text`, nullable.
- `media_ref: str | None` `String(500)`, nullable (a URL/id into Module 18; not validated here).
- `scheduled_at: datetime | None` `DateTime(timezone=True)`, nullable.
- `published_at: datetime | None` `DateTime(timezone=True)`, nullable.

`MarketingChannel(UUIDMixin, TimestampMixin, Base)` → `marketing_channels`:
- `startup_id` FK CASCADE, `index=True`.
- `key: ChannelKey` `Enum(..., length=20)`, non-null.
- `status: ChannelStatus` `Enum(..., length=12)`, non-null, `default=not_started`.
- `notes: str | None` `Text`, nullable.
- `__table_args__ = (UniqueConstraint("startup_id", "key", name="uq_marketing_channel_startup_key"),)`.

Registered in `app/db/models/__init__.py`. Migration `0033_marketing_calendar_channels`, `down_revision="0032_journal_prompts"`, single linear head.

> Table names: the PRD calls these `content_calendar` and `channels`. We keep `content_calendar` (specific enough) and use `marketing_channels` (the bare `channels` is too generic and risks colliding with future modules) — noted in the FE guide.

### Service — `app/services/marketing/service.py`

Calendar:
- `create_entry(db, *, startup_id, created_by, data) -> ContentCalendarEntry`
- `list_entries(db, *, startup_id, date_from=None, date_to=None, channel=None, status=None) -> list[...]` — range filter on `scheduled_at` (for month/week views); `date_from`/`date_to` inclusive; unscheduled drafts returned when no range given.
- `get_entry(db, *, startup_id, entry_id)` — tenancy-scoped; `NotFound` if missing/other tenant.
- `update_entry(db, *, startup_id, entry_id, data)` — partial update (title, channel, body, media_ref, scheduled_at, status). On a transition **into** `published` (i.e. `published_at is None` and new status is `published`): set `published_at = now(UTC)` and publish the `marketing.post.published` event once. Validation: setting `status=scheduled` requires a `scheduled_at` (else `422`).
- `delete_entry(db, *, startup_id, entry_id)`

Channels:
- `list_channels(db, *, startup_id) -> list[MarketingChannel]` — **lazy-seed**: create any of the 8 `ChannelKey` rows missing for this startup (idempotent: query existing keys, insert the rest inside a SAVEPOINT, tolerate the unique-constraint race like `get_or_create_*` elsewhere), return all 8 ordered by the enum order.
- `update_channel(db, *, startup_id, key, data)` — update `status` and/or `notes`; `NotFound` if the key row does not exist yet (callers `GET` first, which seeds).

Overview:
- `overview(db, *, startup_id) -> dict` — `scheduled_this_week` (count of entries with `status=scheduled` and `scheduled_at` within the current ISO week), `active_channels` (count of `marketing_channels` with `status=active`), and the deferred fields as constants: `active_campaigns: None`, `top_channel_by_conversions: None`, `ai_content_ideas: None`.

Publish event payload: `{"entry_id": str, "startup_id": str, "title": str, "channel": str, "created_by": str | None}`. Emitted via `event_bus.publish(db, "marketing.post.published", payload)`.

### Endpoints — `app/api/v1/endpoints/marketing.py`, mounted `prefix="/marketing"`

All depend on `_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)` + `get_verified_user` + `get_db`, returning `success_response(...)`. Mutating endpoints `db.commit()` before returning (the `get_db` session does not auto-commit — established pattern, e.g. `dashboard.py`).

| Method & path | Purpose |
| --- | --- |
| `GET /marketing` | Overview stat strip (10.1) |
| `POST /marketing/calendar-entries` | Create entry |
| `GET /marketing/calendar-entries?from=&to=&channel=&status=` | List (date-range for month/week) |
| `GET /marketing/calendar-entries/{id}` | Detail |
| `PATCH /marketing/calendar-entries/{id}` | Edit / reschedule / status transition (publish) |
| `DELETE /marketing/calendar-entries/{id}` | Delete |
| `GET /marketing/channels` | List (lazy-seeds the 8) |
| `PATCH /marketing/channels/{key}` | Update a channel's status/notes |

Router registered in `app/api/v1/api.py`: `api_router.include_router(marketing.router, prefix="/marketing", tags=["marketing"])`.

### Notification wiring — `app/services/notifications/registry.py`

Add a subscriber for `marketing.post.published` that calls `create_notifications(db, user_ids=<founders + created_by>, startup_id=..., type="marketing.post.published", title=f"Scheduled post published: {title}", body=..., data={"entry_id": ...})`. Recipients: the startup's active founder memberships plus the entry's `created_by` (deduped). Follows the existing registry pattern; the handler runs in its own SAVEPOINT (per the event bus contract) so a notification failure never breaks the publish.

## Data flow / FE contract

- Calendar entry response: `id, title, channel, status, body, media_ref, scheduled_at, published_at, created_by, created_at, updated_at`.
- Channel response: `key, status, notes` (+ ids/timestamps). `GET /marketing/channels` always returns all 8, seeded on first read.
- Overview response: `{scheduled_this_week: int, active_channels: int, active_campaigns: null, top_channel_by_conversions: null, ai_content_ideas: null}` — deferred fields explicitly null so the FE knows they're coming.
- Publishing = `PATCH .../{id}` with `status: "published"`; response carries the set `published_at`.

## Error handling / concurrency

- Tenancy: every read/write filters by `startup_id` from the membership; cross-tenant access → `NotFound`.
- Channel lazy-seed tolerates the concurrent-first-read race via SAVEPOINT + re-select (mirrors `get_or_create_enrollment` / dashboard briefing).
- Publish is idempotent: the event fires only on the `published_at is None → published` transition, so re-`PATCH`ing an already-published entry does not re-emit or reset `published_at`.
- Endpoints commit; services end with `db.flush()` and never commit (only the endpoint owns the request txn). Event handlers never commit (runner/bus contract).
- Validation: `title` required and non-blank; `status=scheduled` requires `scheduled_at`; `channel` must be a valid `ChannelKey`; `422` otherwise.

## Testing

- **Unit — models/migration:** table columns + unique constraint; migration `0033` round-trip + `alembic check` drift-clean; single head.
- **Unit — service:** create/list/get/update/delete with tenancy isolation (other-tenant → NotFound); date-range list filters; `status=scheduled` without `scheduled_at` → validation error; publish transition sets `published_at` + emits the event exactly once (spy on the bus), and re-publish does not re-emit; channel lazy-seed creates exactly 8 idempotently; `update_channel` on a missing key → NotFound.
- **Unit — endpoints:** RBAC (a `mentor`/`investor` membership → `403`; `founder` and `team_member` → allowed); envelope shape; overview returns the computed ints + explicit nulls.
- **Unit — notification:** publishing an entry creates a "Scheduled post published: {title}" notification for founders + creator; a non-publish update creates none.
- **E2E (live):** create entry → list (range) → reschedule (PATCH scheduled_at) → publish (PATCH status) → assert `published_at` + notification appears → channels list (8 seeded) → update a channel → overview. Capture every response body to `e2e/_captures/marketing/`.
- Coverage ≥ 95%; DB-clean unit tests.

## Security & privacy

- All endpoints workspace-scoped and role-gated (`founder`, `team_member`). No new PII. `media_ref` is an opaque string, not fetched or trusted server-side in S1.

## FE impact (integration guide)

New `docs/fe-integration-guide-marketing-calendar.md`: every endpoint with **real request/response bodies pasted verbatim from the e2e captures**, status codes, auth (founder/team_member only → 403 otherwise), the fixed `ChannelKey`/`ChannelStatus`/`ContentStatus` enums, the "publish = PATCH status" flow, and the Overview's deferred-null fields (with a note on which slice fills each). Table-name note (`content_calendar`, `marketing_channels`).

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment.
- **Reproduce CI locally & green before push** via the pinned toolchain: black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%, **Migrations (round-trip + drift) — one new migration `0033`, single head**, e2e.
- Enum style `enum.StrEnum` + `Enum(Cls, native_enum=False, length=N)`; FK columns `index=True`; DB-clean unit tests; services `db.flush()` only, endpoints commit; event handlers never commit.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live captures.

## Follow-ups

Multi-channel entries; auto-publish scheduler (hook Module 20 cron); media upload integration with Module 18; granular per-module "Marketing grant"; the deferred Overview stats (campaigns S2, conversions S5, AI ideas S3); `marketing.campaign.*` events (S2).
