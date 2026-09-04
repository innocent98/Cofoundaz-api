# Design — Module 08 Business Builder · Slice 2 (Typed Artifacts)

> **Status:** Approved (brainstorm) · **Date:** 2026-09-04 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 08, screens 08.6–08.9), the UI
> handoff `../cofoundaz/app/(dashboard)/business-builder/*`, and **Slice 1** (canvas core —
> `docs/superpowers/specs/2026-09-01-business-builder-canvas-design.md`, merged into `develop` via
> PR #39).
>
> Built in `feat/business-builder-records`, **migration `0012`** (head is `0011_business_canvases`).
> **Feature PR targets `develop`** (→ staging), not `main`, per the 2026-09-03 CI/CD restructure.

---

## 1. Scope (Slice 2)

The four **typed/list artifacts** of Business Builder — Customer Personas, Revenue Model, Competitive
Analysis, Pricing Strategy — built on **one generic `business_records` table + a per-kind
Pydantic-schema registry**, giving all four a single uniform CRUD surface parameterized by `{kind}`.
This continues Slice 1's "generic table + per-type registry" philosophy, extended from flat blocks to
richer per-kind shapes (including Pricing's nested `tiers`). AI generation stays deferred behind the
same job-enqueue seam Slice 1 established.

**In scope:** the `business_records` table + `RecordKind` enum + per-kind schema registry; list/create/
update/delete CRUD for all four kinds; per-kind validation; the deferred `ai-fill` job; extension of
Slice 1's `overview` to include the four record kinds; the `business.artifact.completed` event.

**Deferred:**

| Deferred | To |
|---|---|
| Real AI generation (personas from Module 09 validation notes, pricing willingness-to-pay advisor) | Module 03 / a worker draining the `business.{kind}.ai_fill` job |
| "Send to financial model" (revenue streams → Finance forecast) | Module 12 |
| Competitor 2×2 positioning-map builder (draggable dots, editable axes) | Slice 3 (with the BC suggest→approve workflow + AI Business Plan generator + export) |
| Record reordering (drag) | a later `PATCH /{kind}/reorder` if the FE needs it; v1 orders by insertion `position` |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Data model | **Generic `business_records` table + per-kind Pydantic-schema registry** (not four bespoke typed tables). Consistent with Slice 1; one table, one CRUD pattern, one migration. `data` is JSONB — acceptable because these artifacts are read back wholesale, not queried by field. |
| 2 | Pricing shape | **A one-record collection**, not a singleton special-case. Its `data = {model_type, tiers:[…]}`. Keeps all four kinds on one uniform CRUD pattern. |
| 3 | Validation | **Per-kind Pydantic model** (`RECORD_SCHEMAS[kind]`), not a flat field registry — handles flat text, string-lists, numbers, enums, and Pricing's nested tier objects uniformly. |
| 4 | Overview | **One heterogeneous list.** Every row (canvas or record) shares `{type, label, status, completion_pct}`; canvas rows keep block detail, record rows add `count`. Preserves Slice 1's overview contract additively. |

## 3. Data model — `business_records` (migration `0012_business_records`)

**New enum** `RecordKind` (`app/db/models/enums.py`): `persona · revenue_stream · competitor · pricing`
(VARCHAR-backed, `native_enum=False, length=20`, matching every other enum column in the codebase).

**New table `business_records`** (UUIDMixin + TimestampMixin):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | `ondelete=CASCADE`, **standalone index** `ix_business_records_startup_id` (per the codebase FK-index convention) |
| `kind` | `RecordKind` enum | VARCHAR(20)-backed |
| `data` | JSONB | per-kind fields, validated by the registry; never `null` |
| `position` | Integer | insertion order within a `(startup_id, kind)`; assigned as `max(position)+1` on create |
| (TimestampMixin) | | |

Index: `ix_business_records_startup_kind (startup_id, kind, position)` — powers ordered list reads.
No unique constraint (all kinds are collections). `down_revision = "0011_business_canvases"`.

## 4. Per-kind registry — `app/services/business/record_defs.py`

`RECORD_SCHEMAS: dict[RecordKind, type[BaseModel]]` maps each kind to a Pydantic model. Validation of a
create/update `data` payload = `RECORD_SCHEMAS[kind](**data)` — a `ValidationError` becomes a `422
VALIDATION_ERROR` (surfaced with `field_errors` in the shape the codebase already uses). The models:

- **`PersonaData`** — `name: str`, `demographics: str = ""`, `goals: list[str] = []`,
  `frustrations: list[str] = []`, `watering_holes: list[str] = []`, `quote: str = ""`.
- **`RevenueStreamData`** — `name: str`, `pricing_basis: str = ""`, `est_monthly: float = 0`,
  `assumptions: str = ""`.
- **`CompetitorData`** — `name: str`, `positioning: str = ""`, `price: str = ""`,
  `strengths: list[str] = []`, `weaknesses: list[str] = []`,
  `threat_level: ThreatLevel = ThreatLevel.medium` (enum `low | medium | high`).
- **`PricingData`** — `model_type: PricingModelType` (enum `subscription | one_time | usage |
  freemium | tiered`), `tiers: list[PricingTier] = []` where
  `PricingTier = {name: str, price: str = "", features: list[str] = []}`.

`name` is the one required field per kind (an unnamed record is meaningless); everything else has a
default so partial records are valid. A `fields(kind) -> list[dict]` helper exposes the kind's field
descriptors (key, type, required, enum choices) for the FE — the FE renders forms from this, never
hardcodes fields (the Slice-1 `block_defs` philosophy).

## 5. Service — `app/services/business/records.py` (new; `overview` edit stays in `service.py`)

- `list_records(db, startup, kind) -> list[BusinessRecord]` — filtered by `(startup_id, kind)`, ordered by `position`.
- `create_record(db, startup, kind, data) -> BusinessRecord` — `validate(kind, data)`; `position = max+1`; `db.add`/`flush`. Emits `business.artifact.completed` on the kind's 0→1 transition.
- `update_record(db, record, data) -> BusinessRecord` — `validate(record.kind, data)`; replace `data`. **Full-replace of `data`** (same PUT semantics as Slice 1 — documented for the FE).
- `delete_record(db, record) -> None`.
- `validate(kind, data) -> dict` — runs the kind's Pydantic model; returns the normalized dict (defaults applied); raises `AppError("VALIDATION_ERROR", …, 422, field_errors=…)` on failure.
- `serialize_record(record) -> {id, kind, data, position}`.
- `_record(db, membership, kind, record_id) -> BusinessRecord` — fetch a record scoped to `(startup_id, kind)`; cross-tenant/unknown → uniform `NotFound()` (404).
- **`overview` extension** (in `service.py`): after the canvas rows, append one row per `RecordKind`:
  `{type: kind.value, label, status, completion_pct, count}` where `status = complete` iff `count ≥ 1`
  (Pricing: iff the record carries a `model_type`), `completion_pct = 100 if complete else 0`. Canvas
  rows are unchanged; record rows add `count` and omit the block fields.

The service never commits — endpoints own `db.commit()`.

## 6. Endpoints (`/api/v1/business-builder`, verified)

Extend `app/api/v1/endpoints/business.py`, reusing Slice 1's `_startup`, `_editor`, and the
`_parse_*`→404 pattern. Reads = member (`require_workspace`); writes = editor (`require_role(founder,
team_member)`, mentor 403); `get_verified_user` everywhere.

| Route | Access | Behaviour |
|---|---|---|
| `GET /business-builder/{kind}` | member | `{ records: [serialize_record…], fields: fields(kind) }`. |
| `POST /business-builder/{kind}` | editor | Create (validated) → **201** `serialize_record`. |
| `PUT /business-builder/{kind}/{id}` | editor | Full-replace `data` (validated) → `serialize_record`. |
| `DELETE /business-builder/{kind}/{id}` | editor | Delete → `{ deleted: true }`. |
| `POST /business-builder/{kind}/ai-fill` | editor | **Deferred job enqueue** (reuse Slice 1): `job_dispatcher.enqueue(db, type="business.{kind}.ai_fill", payload={startup_id, kind}, startup_id=…)` → **202** `{job_id, status:"queued"}`; writes no record. |

`{kind}` path segments: `personas`, `revenue-streams`, `competitors`, `pricing`, mapped to `RecordKind`
by `_parse_kind(kind: str) -> RecordKind` (unknown segment → `NotFound()` 404, never 422). An unknown
`{id}` or a record belonging to another workspace → uniform `404`.

## 7. Errors, events, config

- **Errors:** reuse the `AppError` taxonomy — `NOT_FOUND` (unknown kind, unknown/cross-tenant id),
  `VALIDATION_ERROR` (bad `data` per the kind schema; 422 with `field_errors`), `FORBIDDEN` (mentor
  write), `EMAIL_NOT_VERIFIED`. **No new error codes.**
- **Events:** `business.artifact.completed` (fire-and-forget, Module 20 consumer) on a kind's first
  record — mirrors Slice 1's completion event, so the FE/Module 20 see one consistent event across all
  Business Builder artifacts.
- **Config:** none new.

## 8. Testing

- **TDD**, real Postgres + per-test rollback; add `create_business_record` factory, reuse Slice-1 factories.
- **Registry/validation:** each kind's valid payload passes and normalizes (defaults applied); a bad
  type / bad enum / missing `name` → 422 `VALIDATION_ERROR`; Pricing's nested `tiers` validate
  (a malformed tier → 422).
- **Service:** create appends `position` (max+1); update full-replaces `data`; delete removes; list is
  position-ordered; overview appends the 4 record rows with correct `start`/`complete` + `count`; the
  0→1 `business.artifact.completed` event fires once per kind (not on the 2nd record).
- **Tenancy/access:** member reads; editor writes; mentor `403`; a record id from another startup → `404`;
  unknown `{kind}` → `404`; verified gate.
- **ai-fill:** enqueues `business.{kind}.ai_fill` with the right payload, writes no record; mentor `403`.
- **Live E2E** (extend `e2e/test_business_builder.py`): create a persona → list (ordered) → update it
  → `GET /overview` shows `personas` `complete` → create a competitor + a pricing record (with tiers) →
  `POST .../ai-fill` → job `queued`. Capture bodies to `e2e/_captures/business/` (records set).
- **FE integration guide** update (`docs/fe-integration-guide-business-builder.md`): the per-kind field
  schemas (`fields(kind)`), the CRUD contract per kind, the **full-replace PUT** note (same trap as
  Slice 1 — send the complete `data`), the overview's new record rows (`count`, shared `status`/
  `completion_pct`), and the ai-fill deferral — every payload from live captures; error shapes labeled
  derived where not captured.

## 9. Plan shape

One implementation plan (`writing-plans`), ~6 TDD tasks, subagent-driven in `feat/business-builder-records`:

1. `RecordKind` (+ `ThreatLevel`, `PricingModelType`) enums + `business_records` model + migration `0012` + `record_defs` registry (4 Pydantic schemas + `fields()`) + `create_business_record` factory
2. Service `records.py` — `validate` + `list/create/update/delete_record` + `serialize_record` + `_record`, and the `overview` extension in `service.py`
3. `GET /{kind}` + `POST /{kind}` (create → 201) + `_parse_kind`
4. `PUT /{kind}/{id}` + `DELETE /{kind}/{id}`
5. `POST /{kind}/ai-fill` (deferred job) + confirm the `business.artifact.completed` wiring
6. Live E2E + smoke surface + SOP + FE integration guide update + checklist reconcile
