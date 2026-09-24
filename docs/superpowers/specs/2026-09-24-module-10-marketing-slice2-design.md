# Module 10 — Marketing Hub, Slice 2: Campaigns + Audience Segments (design)

**Status:** approved-for-planning
**Date:** 2026-09-24
**Module:** 10 (Marketing Hub) — Slice 2 of 5. Backend API only.
**Depends on:** Slice 1 (merged, PR #100 — the `marketing` module, `ChannelKey` enum, RBAC, event/notification wiring), Module 08's `business_records` (personas), Module 20 notifications. Nothing here is blocked.

## Goal

Ship campaign planning and reusable audience segments: create **audience segments** (name + rule definition + estimated size + optional linked persona) and **campaigns** (objective, budget, a channel-budget mix, and the segments they target), then move campaigns through a draft→active→completed lifecycle, each transition emitting a `marketing.campaign.*` event + in-app notification.

## Why

Second of Module 10's five slices (CRUD spine → **campaigns/segments** → AI layer → SEO → analytics). Campaigns are the marketing hub's central planning object; segments are the reusable audience definitions campaigns target. Both are pure backend CRUD + a lifecycle state machine — a good fit before the AI layer (Slice 3) wires the channel-plan recommender onto the campaign create flow.

## Decisions settled in brainstorming

| Decision | Choice | Consequence |
| --- | --- | --- |
| `channel_mix` shape | **`{ChannelKey: percent}`** (0–100 per channel) | Matches the wizard's editable sliders; per-channel amount is derived (`budget × pct`). Validate keys ∈ `ChannelKey` and each value 0–100; do **not** force the sum to 100. |
| Campaign ↔ segments | **`campaign_segments` join table** (many-to-many) | Referential integrity + the segment's "used-by campaigns" query (PRD 10.7); cascades cleanly. |
| Campaign status lifecycle | **draft / active / paused / completed** | launch = draft→active (`launched_at` + `marketing.campaign.launched`); complete = active→completed (`completed_at` + `marketing.campaign.completed`); pause/resume = active↔paused. No `cancelled` (delete covers it) — YAGNI. |
| Budget type | **integer minor units (cents)** | Money-safe; FE formats to currency. |
| Segment `definition` | **opaque JSONB blob** (not executed) | S2 stores/returns the rule-builder tree; no segmentation engine. `est_size` is user-entered. |
| `persona_id` link | **nullable FK → `business_records.id`, validated `kind=persona`** | Links a segment to a persona (a `business_records` row); create-time validation rejects a non-persona record. |
| Status transitions API | **`PATCH` status, guarded in the service** | Consistent with Slice 1's publish-on-PATCH; the service enforces legal transitions (invalid → 422). |

## Scope

### In scope
1. Enums: `CampaignObjective`, `CampaignStatus` (`app/db/models/enums.py`).
2. Models + migration `0034`: `AudienceSegment` (`audience_segments`), `Campaign` (`campaigns`), `CampaignSegment` (`campaign_segments` join).
3. Schemas (extend `app/schemas/marketing.py`).
4. Service — **new focused modules** `app/services/marketing/segments.py` and `app/services/marketing/campaigns.py` (Slice 1's `service.py` stays calendar/channels; campaigns+segments are a distinct concern kept in their own files).
5. Endpoints (extend `app/api/v1/endpoints/marketing.py`): segments CRUD + used-by; campaigns CRUD + guarded status transitions.
6. Events `marketing.campaign.launched` / `marketing.campaign.completed` + two notifications-registry rows.
7. Tests (unit + e2e), FE guide, SOP, checklist.

### Out of scope (later slices / deferred)
- AI **channel-plan recommend** (`POST /marketing/channel-plan/recommend`, job) → **Slice 3**.
- Campaign **performance metrics/charts** (`metrics` stays `{}` in S2) → **Slice 5**; the "Campaign performance summary ready" notification is a Slice 5 concern.
- Campaign wizard **Content step** (attach/generate assets → link `content_calendar` entries to a campaign) — deferred follow-up.
- Executing segment rules / computing `est_size` from a rule engine — deferred (opaque + user-entered in S2).
- A `cancelled` status.

## Architecture

### Enums — `app/db/models/enums.py`
```python
class CampaignObjective(enum.StrEnum):
    awareness = "awareness"
    leads = "leads"
    sales = "sales"
    launch = "launch"

class CampaignStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"
```
Mapped via `Enum(Cls, native_enum=False, length=N)`.

### Data model — `app/db/models/marketing.py` (extend) + migration `0034`

`AudienceSegment(UUIDMixin, TimestampMixin, Base)` → `audience_segments`:
- `startup_id` FK `startups.id` CASCADE, `index=True`.
- `name` `String(200)`, non-null.
- `definition` `JSONB`, non-null, `default=dict` (opaque rule tree).
- `est_size` `Integer`, nullable (user-entered).
- `persona_id` `PGUUID` nullable, FK `business_records.id` `ondelete="SET NULL"`, `index=True`.

`Campaign(UUIDMixin, TimestampMixin, Base)` → `campaigns`:
- `startup_id` FK CASCADE, `index=True`.
- `name` `String(200)`, non-null.
- `objective` `Enum(CampaignObjective, native_enum=False, length=12)`, non-null.
- `budget` `Integer`, non-null, `default=0`, `server_default="0"` (cents).
- `channel_mix` `JSONB`, non-null, `default=dict` (`{ChannelKey: percent}`).
- `status` `Enum(CampaignStatus, native_enum=False, length=12)`, non-null, `default=draft`.
- `metrics` `JSONB`, non-null, `default=dict` (empty until Slice 5).
- `period_start` `Date` nullable, `period_end` `Date` nullable.
- `launched_at` / `completed_at` `DateTime(timezone=True)` nullable.

`CampaignSegment(UUIDMixin, TimestampMixin, Base)` → `campaign_segments`:
- `campaign_id` FK `campaigns.id` CASCADE, `index=True`.
- `segment_id` FK `audience_segments.id` CASCADE, `index=True`.
- `__table_args__ = (UniqueConstraint("campaign_id", "segment_id", name="uq_campaign_segment"),)`.

Registered in `app/db/models/__init__.py`. Migration `0034_marketing_campaigns_segments`, `down_revision="0033_marketing_calendar_channels"`, single head.

### Service — `app/services/marketing/segments.py` (new)
- `create_segment(db, *, startup_id, data) -> AudienceSegment` — validates `persona_id` (if given) references a `business_records` row of this startup with `kind == RecordKind.persona`, else `422`.
- `list_segments(db, *, startup_id)`, `get_segment(db, *, startup_id, segment_id)` (NotFound on miss/other tenant), `update_segment(...)`, `delete_segment(...)`.
- `segment_campaigns(db, *, startup_id, segment_id) -> list[Campaign]` — the "used-by campaigns" list, via the join.

### Service — `app/services/marketing/campaigns.py` (new)
- `create_campaign(db, *, startup_id, data) -> Campaign` — validates every `segment_id` in `data.segment_ids` belongs to this startup (else `422`); validates `channel_mix` keys ∈ `ChannelKey` and values 0–100; creates the campaign + `campaign_segments` join rows.
- `list_campaigns(db, *, startup_id)`, `get_campaign(...)`, `update_campaign(db, *, startup_id, campaign_id, actor_id, data)` — partial update including **guarded status transitions**, and re-syncing `segment_ids` when provided.
- `delete_campaign(...)`.
- **Transition guard** (in `update_campaign`, on a `status` change): legal moves are `draft→active` (set `launched_at`, emit `marketing.campaign.launched`), `active→completed` (set `completed_at`, emit `marketing.campaign.completed`), `active→paused`, `paused→active`. Any other transition → `422`. Events emitted after `db.flush()`; payload `{startup_id, campaign_id, name, actor_id}`.

### Endpoints — `app/api/v1/endpoints/marketing.py` (extend), all `require_role(founder, team_member)`, commit before returning

| Method & path | Purpose |
| --- | --- |
| `POST /marketing/segments` | Create segment |
| `GET /marketing/segments` | List segments |
| `GET /marketing/segments/{id}` | Segment detail |
| `PATCH /marketing/segments/{id}` | Update segment |
| `DELETE /marketing/segments/{id}` | Delete segment |
| `GET /marketing/segments/{id}/campaigns` | Campaigns using this segment (used-by) |
| `POST /marketing/campaigns` | Create campaign (with `segment_ids`, `channel_mix`) |
| `GET /marketing/campaigns` | List campaigns |
| `GET /marketing/campaigns/{id}` | Campaign detail (incl. segments, metrics `{}`) |
| `PATCH /marketing/campaigns/{id}` | Update / guarded status transition (launch/complete/pause/resume) |
| `DELETE /marketing/campaigns/{id}` | Delete campaign |

### Notification wiring — `app/services/notifications/registry.py`
Two `SPECS` rows using `_members_minus_actor` + per-payload titles:
- `marketing.campaign.launched` → `f"Campaign launched: {name}"`
- `marketing.campaign.completed` → `f"Campaign completed: {name}"`
Unmapped in `categories.py` (in-app only; email deferred, same as Slice 1).

## Data flow / FE contract
- Campaign response: `id, name, objective, budget, channel_mix, status, metrics, segment_ids (or segment summaries), period_start, period_end, launched_at, completed_at, timestamps`.
- Segment response: `id, name, definition, est_size, persona_id, timestamps`.
- Status transitions are `PATCH .../{id}` with `status`; the response carries the new timestamps.
- `channel_mix` is `{channel_key: percent}`; the FE derives per-channel spend from `budget`.

## Error handling / concurrency
- Tenancy on every read/write via `startup_id`; cross-tenant → `NotFound`.
- Cross-tenant/foreign `segment_ids` or a non-persona `persona_id` → `422` at create/update.
- Illegal status transition → `422` (the guard is the single source of transition truth).
- Launch/complete idempotency: the event fires only on the actual transition (guarded by current status + the null `launched_at`/`completed_at`), so re-issuing the same status is a no-op / rejected.
- Services `db.flush()` only; endpoints `db.commit()`; event handlers never commit.

## Testing
- **Unit — models/migration:** columns + join unique constraint; migration `0034` round-trip + drift-clean; single head.
- **Unit — segments:** CRUD + tenancy; `persona_id` validation (persona ok; non-persona/other-tenant → 422); `segment_campaigns` returns only campaigns using it.
- **Unit — campaigns:** create with `segment_ids` (foreign/other-tenant id → 422; join rows created); `channel_mix` validation (bad key / out-of-range → 422); each legal transition sets the right timestamp + emits exactly one event; each illegal transition → 422; re-issuing a terminal status → no duplicate event.
- **Unit — notification:** launching/completing notifies the workspace minus the actor with the right title; other updates notify no one.
- **Unit — endpoints:** RBAC (mentor/investor → 403 on all campaign/segment routes); envelope shapes; used-by endpoint.
- **E2E (live):** create segment (with persona link) → create campaign targeting it → list → launch (assert launched_at + notification) → pause → resume → complete (assert completed_at) → segment used-by shows the campaign. Capture all bodies to `e2e/_captures/marketing/` (campaign_*, segment_*).
- Coverage ≥ 95%; DB-clean unit tests.
- **CodeQL hygiene in tests:** no mutating call inside an `assert` (extract to a var first); no implicit string concatenation inside a list literal (lift multi-fragment strings to a named variable) — both are enforced CodeQL queries on this repo.

## Security & privacy
- Workspace-scoped + role-gated (founder/team_member). `budget`/`channel_mix`/`metrics` are non-PII. `persona_id` references the same workspace's business record only.

## FE impact (integration guide)
New `docs/fe-integration-guide-marketing-campaigns.md`: every segment + campaign endpoint with **request/response bodies verbatim from the e2e captures**, the `CampaignObjective`/`CampaignStatus` enums, the `channel_mix` `{channel: percent}` shape (FE derives spend from budget in cents), the guarded transition rules (which status moves are legal; 422 otherwise), the used-by endpoint, and that `metrics` is `{}` until Slice 5. Cross-reference the Slice 1 calendar guide.

## Global constraints (carried into the plan)
- **No AI attribution** anywhere.
- **Reproduce CI locally & green before push:** black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%, **Migrations (round-trip + drift) — one new migration `0034`, single head**, e2e, **CodeQL** (write tests to avoid the two flagged patterns above).
- Enum style `enum.StrEnum` + `Enum(Cls, native_enum=False, length=N)`; FK columns `index=True`; services `db.flush()` only, endpoints commit; event handlers never commit.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live captures.

## Follow-ups
AI channel-plan recommend (S3); campaign performance metrics + "summary ready" notification (S5); campaign Content step (attach/generate assets, link calendar entries); segment rule execution + computed `est_size`; a `cancelled` status if the FE needs a kept terminal-cancel; multi-channel calendar entries (carried from S1).
