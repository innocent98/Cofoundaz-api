# Module 08 Business Builder — Slice 3 Design: Suggestions Workflow + Positioning Map

**Date:** 2026-09-08
**Module:** 08 Business Builder (final buildable slice)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)
**Depends on:** Slice 1 (Canvas Core, PR #39) + Slice 2 (Typed Records, PR #46), both merged to `develop`.

---

## 1. Context & Scope

Slices 1–2 shipped the Business Builder's artifacts:

- **Canvases** (`business_canvases`): typed block documents (`business_model`, `lean`,
  `value_prop`, `mission_vision`, `swot`), full-replace `PUT` with optimistic-concurrency
  `version`, applied via `save_canvas(db, canvas, blocks, expected_version)`.
- **Records** (`business_records`): list artifacts (`persona`, `revenue_stream`,
  `competitor`, `pricing`) with per-kind Pydantic validation (`RECORD_SCHEMAS`), applied via
  `create_record` / `update_record` / `delete_record`.

Slice 3 delivers the two remaining **buildable** pieces of the PRD's Module 08:

1. **Business-consultant suggest → approve/reject workflow** — collaborators propose changes
   to the founder's artifacts; editors accept or decline them. This is the substantial piece.
2. **Competitor 2×2 positioning map** — editable axes + per-competitor coordinates rendered
   as a 2×2 map. Small.

### Explicitly deferred (dependency-blocked, NOT in this slice)

The PRD's **AI Business Plan generator (08.11)** is deferred until its dependencies exist:

- **Module 03 (AI Co-Founder)** — no LLM provider is wired yet, so there is nothing to
  generate the plan.
- **Module 18 (Documents)** — no `Document` model/seam exists, so the generated plan
  (`business_plans.document_id`) would have nowhere to live.

Building only a readiness-checklist + deferred-job stub now would add almost nothing over the
existing `GET /business-builder/overview`. When Modules 03 and 18 land, the plan generator gets
its own slice. **After Slice 3, Module 08 is "complete except the plan generator, which is
dependency-blocked."**

### Non-goals

- No real-time notifications for suggestions (events are published but unconsumed until a
  notifications module — consistent with the existing `business.artifact.completed` event).
- No granular field-level diff/merge engine — suggestions carry a full proposed payload for
  one target; the FE renders before/after.
- No reject-reason free-text field in v1 (follow-up).
- No positioning-map history/versioning.

---

## 2. Suggestions Workflow

### 2.1 Concept

A **suggestion** is a *proposed change to exactly one artifact*, expressed as an
**operation + target + proposed payload**. It is created by any workspace member (canonically a
`business_consultant`, who cannot edit artifacts directly), and resolved (approved/rejected) by
an editor (`founder` / `team_member`).

**On approve, the operation is applied through the exact same service functions the editor
endpoints already call** — so every existing validator, the canvas optimistic-concurrency
check, and the `business.artifact.completed` event fire unchanged. No parallel apply path.

The FE renders the before/after diff itself from the target's live `current` state (returned on
read) vs. the suggestion's `payload`.

### 2.2 Operations (full set — approved)

| `op` | Target | Payload | Applied via |
|---|---|---|---|
| `canvas_update` | `{"canvas_type": <CanvasType>}` | `{"blocks": {...}}` | `get_or_create_canvas` → `save_canvas(blocks, base_version)` |
| `record_create` | `{"kind": <RecordKind>}` | `{"data": {...}}` | `create_record(kind, data)` |
| `record_update` | `{"kind": <RecordKind>, "record_id": <uuid>}` | `{"data": {...}}` | `_record(...)` → `update_record(data)` |
| `record_delete` | `{"kind": <RecordKind>, "record_id": <uuid>}` | `null` | `_record(...)` → `delete_record` |

### 2.3 Data model — `business_suggestions` (new table)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin`, `server_default now()` |
| `startup_id` | UUID FK→`startups.id` `ON DELETE CASCADE`, `index=True` | tenancy scope |
| `author_id` | UUID FK→`users.id`, `index=True` | who proposed |
| `op` | `Enum(SuggestionOp, native_enum=False, length=20)` | see 2.2 |
| `target` | `JSONB` | structured target (see 2.2) |
| `payload` | `JSONB`, nullable | proposed blocks/data; `null` for `record_delete` |
| `base_version` | `Integer`, nullable | `canvas_update` only — canvas `version` at create time |
| `note` | `Text`, nullable | author's rationale |
| `status` | `Enum(SuggestionStatus, native_enum=False, length=20)`, `server_default 'pending'` | `pending` / `approved` / `rejected` |
| `resolved_by_id` | UUID FK→`users.id`, nullable | editor who resolved |
| `resolved_at` | timestamptz, nullable | |

**Indexes:** FK `startup_id` gets a standalone `index=True` (project convention) **and** a
composite `Index("ix_business_suggestions_startup_status", "startup_id", "status")` for the
list-by-status query.

**New enums** (in `app/db/models/enums.py`, following the `enum.StrEnum` +
`native_enum=False` VARCHAR convention used by all 22 existing enum columns):

```python
class SuggestionOp(enum.StrEnum):
    canvas_update = "canvas_update"
    record_create = "record_create"
    record_update = "record_update"
    record_delete = "record_delete"

class SuggestionStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
```

### 2.4 Validation at **create** time

A suggestion is validated when filed, so a proposer cannot store a structurally-broken change:

- `canvas_update`: `_parse_type` the canvas_type (404 if unknown); `validate_blocks` the
  proposed blocks (422 on unknown/mistyped block); capture the target canvas's current
  `version` into `base_version` (lazily creating the canvas if absent, mirroring the editor path).
- `record_create`: `validate(kind, data)` (422 on invalid data).
- `record_update` / `record_delete`: `validate(kind, data)` for update; resolve the target
  record scoped to the caller's `startup_id` — **404 (uniform)** if missing or cross-tenant.

`POST /suggestions` publishes `business.suggestion.created`.

### 2.5 Apply at **approve** time (edge cases)

Approve re-resolves and re-applies inside the request transaction:

- `canvas_update`: if the canvas `version` has moved since `base_version` →
  **409 `CANVAS_VERSION_CONFLICT`** (existing error). The txn rolls back, the suggestion stays
  `pending`, and the founder reloads/re-evaluates.
- `record_update` / `record_delete`: if the target record was deleted since → **404**; txn
  rolls back, suggestion stays `pending`.
- `record_create` / all: payload re-validated at apply for safety (schema-drift guard).

On successful apply: `status='approved'`, `resolved_by_id`, `resolved_at` set; publish
`business.suggestion.approved`. Because apply reuses `save_canvas`/`create_record`, the
`business.artifact.completed` event still fires when the change completes an artifact.

### 2.6 State machine

`pending → approved` | `pending → rejected` (terminal). Approve/reject on a non-pending
suggestion → **409 `SUGGESTION_NOT_PENDING`**. Reject sets `status='rejected'`,
`resolved_by_id`, `resolved_at`; publishes `business.suggestion.rejected`.

### 2.7 Endpoints

All under the existing `/business-builder` router. **Route-shadowing guard:** the literal
`/suggestions` and `/positioning-map` routes MUST be declared **before** the existing `/{kind}`
catch-all (a bare `GET /{kind}` would otherwise capture `GET /suggestions`). This mirrors the
Slice-2 ordering where `/{kind}` is declared after the literal `/overview` and `/canvases/*`.

| Method | Path | Auth dependency | Success | Body |
|---|---|---|---|---|
| `POST` | `/business-builder/suggestions` | `require_workspace` (any member) | `201` | `{op, target, payload?, note?}` |
| `GET` | `/business-builder/suggestions` | `require_workspace` | `200` | query `?status=pending\|approved\|rejected` (optional) |
| `POST` | `/business-builder/suggestions/{id}/approve` | `_editor` (founder/team_member) | `200` | — |
| `POST` | `/business-builder/suggestions/{id}/reject` | `_editor` | `200` | — |

**Role model:** *propose* = any workspace member (mentors, accountants, and the canonical
`business_consultant` can all suggest even though they cannot edit directly). *Approve/reject*
= editors only — accepting a proposed change requires the same authority as making it directly.
Cross-tenant access to a suggestion by id → uniform **404**.

### 2.8 Serialization (list + create response)

Each suggestion serializes with its target's **live `current` state** so the FE can render the
diff in one round trip:

```jsonc
{
  "id": "…",
  "op": "record_update",
  "target": {"kind": "competitor", "record_id": "…"},
  "payload": {"data": { … proposed … }},
  "base_version": null,
  "current": { … live blocks/data, or null for record_create … },
  "note": "Positioning is vague — tie it to the ICP.",
  "status": "pending",
  "author": {"id": "…", "name": "…"},
  "resolved_by": null,
  "resolved_at": null,
  "created_at": "…"
}
```

`current` is computed at read time: canvas blocks+version for `canvas_update`; record data for
`record_update`/`record_delete`; `null` for `record_create`.

---

## 3. Competitor 2×2 Positioning Map

### 3.1 Coordinates live on the competitor record

`CompetitorData` (in `record_defs.py`) gains two optional coordinates:

```python
map_x: float | None = Field(default=None, ge=0, le=1)
map_y: float | None = Field(default=None, ge=0, le=1)
```

- Edited through the **existing** `PUT /business-builder/competitors/{id}` — no new write path.
- Lifecycle-coupled to the competitor (deleted with it → no dangling coordinates).
- **No record-data migration:** `data` is JSONB; existing competitor rows simply lack the keys
  and deserialize with `None` defaults. `fields(competitor)` automatically surfaces the two new
  descriptors to the FE.

### 3.2 Editable axes — `business_positioning_maps` (new singleton table)

One row per startup, lazily get-or-created like a canvas (SAVEPOINT + re-select race guard,
mirroring `get_or_create_canvas`).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |
| `startup_id` | UUID FK→`startups.id` CASCADE, `index=True`, **UNIQUE** | one map per startup |
| `axes` | `JSONB`, `server_default` the default axes | axis config |

Default axes:

```json
{"x": {"label": "Price", "low": "Low", "high": "High"},
 "y": {"label": "Quality", "low": "Low", "high": "High"}}
```

### 3.3 Endpoints

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/business-builder/positioning-map` | `require_workspace` | `{axes, competitors: [{id, name, x, y, threat_level}]}` assembled from competitor records (`x=data.map_x`, `y=data.map_y`); lazily creates the axes row |
| `PUT` | `/business-builder/positioning-map` | `_editor` | body `{axes}`; shape-validated (x/y each with `label`/`low`/`high` strings); returns the saved map |

---

## 4. Cross-cutting

### 4.1 Migrations

Two new revisions, **chained onto the current `develop` head (`0012_business_records`)**:

1. `business_suggestions` (table + 2 enum-backed VARCHAR columns + composite index)
2. `business_positioning_maps` (singleton table, unique `startup_id`, `axes` JSONB with
   `server_default`)

**Coordination note:** the open journal PR #37 introduces `0013_journal` chained off `0011`.
The numeric prefix of these two revisions (and the single-head invariant) will be settled at
**build time** against the then-current `develop` head — if #37 merges first, these chain onto
`0013_journal` and take `0014`/`0015`; otherwise they take `0013`/`0014`. `alembic check` must
report a single head and zero drift before push.

### 4.2 Events (published, unconsumed)

`business.suggestion.created`, `business.suggestion.approved`, `business.suggestion.rejected`
via `event_bus`, payload `{startup_id, suggestion_id, op}`. Consistent with the existing
`business.artifact.completed` event; no consumer until a notifications module ships.

### 4.3 Errors

- `SUGGESTION_NOT_PENDING` (409) — approve/reject a resolved suggestion.
- `CANVAS_VERSION_CONFLICT` (409) — existing error, reused on stale `canvas_update` approve.
- `NotFound` (404) — unknown suggestion id / cross-tenant / target record gone at apply.
- `VALIDATION_ERROR` (422) — existing `AppError`, reused for bad blocks/data at create.

### 4.4 `db.commit()` discipline

`get_db()` does not auto-commit. Every write endpoint here (`POST /suggestions`, approve,
reject, `PUT /positioning-map`) and the lazy-create read endpoints (`GET /positioning-map`)
MUST call `db.commit()` — the same rule that bit the journal endpoints.

---

## 5. Testing

**Unit (real Postgres, per-test rollback):**
- Suggestion create: one happy path per `op`; 404 unknown canvas_type / cross-tenant record;
  422 bad blocks / bad data.
- Approve applies each `op` correctly (canvas blocks replaced + version bumped; record
  created/updated/deleted) and transitions to `approved`.
- Reject transitions to `rejected` without touching the target.
- State machine: approve/reject a resolved suggestion → 409.
- Apply edge cases: stale `canvas_update` → 409 `CANVAS_VERSION_CONFLICT`; deleted target
  record → 404; both leave status `pending`.
- Role matrix: `business_consultant` can create but gets 403 on approve/reject; `mentor`
  (viewer) can create + list; non-member → 404; editor can approve/reject.
- `business.artifact.completed` still fires when an approved suggestion completes an artifact.
- Positioning map: lazy get-or-create; axes update round trip; competitor `map_x`/`map_y`
  round trip via existing record PUT; `GET` assembles axes + competitor coordinates; bad axes
  shape → 422; coord out of `[0,1]` → 422.

**Live e2e (`scripts/e2e_run.sh`):** BC-suggests-a-competitor-edit → founder-approves →
artifact reflects it, end to end; positioning-map axes edit + coordinate round trip. Capture
every request/response body verbatim.

**FE integration guide:** `docs/fe-integration-guide-business-builder-suggestions.md` — every
payload/status/error copied verbatim from the captured live responses; the create/approve/reject
state machine and the `current`-vs-`payload` diff contract documented; the positioning-map
assembly and coordinate-on-record trap called out.

---

## 6. File structure

| File | Change |
|---|---|
| `app/db/models/enums.py` | add `SuggestionOp`, `SuggestionStatus` |
| `app/db/models/business.py` | add `BusinessSuggestion`, `BusinessPositioningMap` |
| `alembic/versions/00NN_business_suggestions.py` | new migration |
| `alembic/versions/00NN_business_positioning_maps.py` | new migration |
| `app/services/business/record_defs.py` | `CompetitorData.map_x/map_y` |
| `app/services/business/suggestions.py` | **new** — create/list/approve/reject/serialize + per-op validate & apply |
| `app/services/business/positioning.py` | **new** — get-or-create map, update axes, assemble view |
| `app/schemas/business.py` | `SuggestionCreate`, `PositioningMapSave` request models |
| `app/api/v1/endpoints/business.py` | 4 suggestion routes + 2 positioning-map routes, declared **before** `/{kind}` |
| `tests/…/business/` | unit tests per §5 |
| `e2e/…` | live suggestion + positioning-map journeys |
| `docs/fe-integration-guide-business-builder-suggestions.md` | verified guide |
| `docs/sop/…` | SOP for the slice |
| `docs/checklist/PROJECT_CHECKLIST.md` | mark Slice 3 items |

---

## 7. Decisions & waivers

- **D1 — `op`+`target`+`payload`+`base_version` expands the PRD's
  `suggestions(entity_ref, diff_json)`.** Heterogeneous canvas/record targets cannot be
  faithfully applied from an opaque string + blob; the structured form lets approve reuse the
  existing service functions and their validation. *Supersedes the PRD's flat column list for
  this entity.*
- **D2 — Any workspace member may create a suggestion**, not only `business_consultant`. The BC
  is the canonical proposer, but mentors/accountants/etc. proposing (and only editors approving)
  is strictly more useful and adds no code. Approve/reject stays editor-only.
- **D3 — Positioning coordinates live on the competitor record**, not in a separate map store,
  so they are lifecycle-coupled and editable through the existing record PUT. Only the *axis
  config* is a singleton row.
- **D4 — Plan generator deferred** (see §1) — not a waiver of scope so much as a dependency gate;
  recorded so the checklist and SOP stay honest about Module 08 being "complete except the
  dependency-blocked plan generator."
- **D5 — No reject-reason field in v1.** Follow-up; the `note` column already exists for the
  proposer's rationale.
```

