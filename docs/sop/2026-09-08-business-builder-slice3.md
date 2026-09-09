# SOP — Business Builder Suggestions + Positioning Map (Module 08, Slice 3)

**What shipped** — the third Business Builder surface, layered on Slice 1 (Canvas Core,
`docs/sop/2026-09-01-business-builder-canvas.md`) and Slice 2 (Typed Artifacts,
`docs/sop/2026-09-04-business-builder-records.md`): (1) a **suggestion workflow** letting a
non-editor member (a `business_consultant`, per PRD §08's "BC suggest mode") propose one of four
edits — `canvas_update`, `record_create`, `record_update`, `record_delete` — which a founder or
team_member reviews via `GET /suggestions?status=pending` and resolves with `POST
/suggestions/{id}/approve` or `.../reject`; and (2) a **competitor positioning map** — editable
2×2 axes (`GET`/`PUT /positioning-map`) plus `map_x`/`map_y` coordinates on the existing
`competitor` record kind (PRD §08.9's "2×2 positioning map builder"). Two new tables
(`business_suggestions`, `business_positioning_maps`; migrations `0013_business_suggestions`,
`0014_business_positioning_maps`), five new endpoints, no changes to any existing Slice 1/2
endpoint or table.

Commits (branch `feat/business-builder-suggestions`, off `develop` post-PR #46): `16b1311`
(schema) → `9c851f8` (suggestion create/validate/list/serialize) → `86c1145` (test coverage for
`_current`) → `28575cc` (approve/reject apply + state machine) → `c35640e` (suggestion endpoints
+ route ordering) → `3a905de` (positioning map) → this task's E2E/SOP/FE-guide/checklist commit
(Task 6 — final task of the 6-task plan, `.superpowers/sdd/2026-09-08-business-builder-suggestions/`).

## Why

Slices 1–2 gave a founder (and a `team_member`) a full editing surface for Business Builder, but
the PRD's access model for Module 08 explicitly carves out a **non-editing collaborator role**:
"BC (suggest mode: edits create pending suggestions F must approve)". Without Slice 3, a
`business_consultant` invited to a workspace had no way to contribute to Business Builder at
all — every write endpoint from Slices 1–2 is `_editor`-gated (founder/team_member only), so a
consultant hit `403 FORBIDDEN` on anything but reading. Slice 3's job is exactly that: give a
non-editor a real, constrained way to propose changes, and give the founder a real, auditable way
to accept or reject them — without touching any of the existing direct-write endpoints or their
contracts. The positioning map is a separate, smaller PRD item (§08.9's 2×2 competitor map) bundled
into the same slice because it shares the same "small, self-contained Business Builder addition"
shape and was next in the module's remaining scope.

## How

**Suggestions are applied through the SAME service functions the direct endpoints already use —
not a parallel "apply a suggestion" code path.** `_apply()` (`app/services/business/
suggestions.py:156`) dispatches on `op` and calls `save_canvas`, `create_record`,
`update_record`, or `delete_record` — the exact same functions `PUT /canvases/{type}`, `POST
/{kind}`, `PUT /{kind}/{id}`, `DELETE /{kind}/{id}` call. This was the central design decision
(recorded in the Slice 3 design spec, `.superpowers/sdd/2026-09-08-business-builder-suggestions/`):
a second "apply a suggestion" implementation of canvas/record writes would be a second place for
the full-replace semantics, the optimistic-concurrency check, and the `business.artifact.completed`
event to drift out of sync with the direct endpoints. Applying through the existing functions
means `save_canvas`'s version check (raising `CanvasVersionConflict`) and every existing
canvas/record write test's behavior apply to a suggestion's approval for free, with zero
duplicated logic to keep in sync. The tradeoff: `_apply()` has to reconstruct the right call
signature per op from the suggestion's generic `target`/`payload` JSONB columns, which is a small
amount of dispatch code, in exchange for zero duplicated write logic.

**A suggestion's `base_version` is captured at CREATE time, but its `current` is recomputed LIVE
at every READ/serialize.** These are two different mechanisms serving two different purposes.
`base_version` (only set for `canvas_update`) is the optimistic-concurrency pin — it's frozen once,
at `create_suggestion`, and compared against the canvas's actual version at approve time
(`_apply`'s `assert s.base_version is not None; save_canvas(db, canvas, ..., s.base_version)`) to
decide 200 vs. `409 CANVAS_VERSION_CONFLICT`. `current` (`_current()`,
`app/services/business/suggestions.py:98`) is a read-time convenience for the FE's diff view — it
re-queries the canvas/record fresh every time `serialize_suggestion` runs, so a suggestion's own
`current` value is DIFFERENT on its create-response vs. its later approve-response if the target
changed in between (live-captured proof: `e2e/_captures/business/suggestions_create_canvas_
update.json` shows `current.version: 1`; the SAME suggestion's `suggestions_approve_canvas_
update.json` shows `current.version: 2`, because approving IS what moved the canvas). This
distinction is called out explicitly in the FE guide (§3) since it's an easy trap for a client to
assume `current` is a frozen "before" snapshot.

**Positioning-map coordinates live on the competitor RECORD (`CompetitorData.map_x`/`map_y`), not
on a separate points table.** The alternative considered (recorded in the design spec) was a
dedicated `positioning_points(startup_id, competitor_id, x, y)` table, joined against
`business_records` to assemble the map. Rejected because a competitor's map position is
conceptually just two more fields on that competitor — same JSONB `data` column every other
`CompetitorData` field already lives in, no new table, no new join, and it round-trips through
the EXACT SAME `validate()`/`create_record`/`update_record` functions Slice 2 already shipped and
tested (`Field(ge=0, le=1)` on both fields, enforced by the same Pydantic model, no bespoke
coordinate-bounds check to write). The `business_positioning_maps` table that DID get added holds
only the map's `axes` — a genuinely separate, singleton-per-startup concept (there's one map
configuration per startup, but N competitors with points on it) that doesn't fit on any one
record. This is also why `PUT /positioning-map`'s request schema (`PositioningMapSave`) has only
an `axes` field — it was never meant to carry competitor coordinates, and an extraneous
`competitors` key in that request body is silently dropped by pydantic (confirmed live, FE guide
§8's "coordinate-on-competitor trap" — not a bug, a consequence of this design decision that
needed to be surfaced loudly for the FE, since a naive drag-and-drop implementation would
plausibly try to `PUT` the new coordinates through the map endpoint and silently no-op).

**Any active member can suggest; only `_editor` (founder, team_member) can approve/reject — the
existing `require_workspace` vs. `require_role` split, unchanged.** `POST /suggestions` uses
`Depends(require_workspace)` (any active member, same as every Business Builder read endpoint);
`approve`/`reject` use `Depends(_editor)`, the exact same dependency Slice 1/2's write endpoints
already use. No new role or permission concept was introduced — `business_consultant` earns no
special-cased access; it's simply a role that is a member (can suggest) but not an editor (cannot
approve). This means a `mentor`, `accountant`, `legal_advisor`, or `investor` could equally suggest
an edit today, even though the PRD copy specifically calls out the BC persona — the role check
doesn't (and by design doesn't need to) distinguish "why" a non-editor is suggesting, only "is this
member an editor."

**`record_delete` suggestions always serialize `payload` back as `null`, regardless of what the
consultant sent.** `create_suggestion`'s `record_delete` branch (`suggestions.py:65-69`)
unconditionally sets `payload = None` before persisting — a delete proposes removing the target,
which needs no payload data, so anything sent is discarded rather than validated-and-rejected.
Confirmed live: `suggestions_create_record_delete.json` shows `"payload": null` even though the
e2e test's request never sent a `payload` key at all (an explicit test of "sends one anyway" was
not added — see Follow-ups).

## What's involved

**Data model / migrations**
- `alembic/versions/0013_business_suggestions.py` — `business_suggestions` (+
  `ix_business_suggestions_startup_id`, `ix_business_suggestions_author_id`, composite
  `ix_business_suggestions_startup_status` on `(startup_id, status)`). `startup_id`/`author_id` FK
  `ON DELETE CASCADE`; `resolved_by_id` FK `ON DELETE SET NULL` (deleting the resolving user
  doesn't destroy suggestion history). `op`/`status` are `native_enum=False` VARCHAR(20), matching
  this codebase's StrEnum-column convention.
- `alembic/versions/0014_business_positioning_maps.py` — `business_positioning_maps` (+
  `ix_business_positioning_maps_startup_id`, `uq_business_positioning_map_startup` — at most one
  map row per startup). `startup_id` FK `ON DELETE CASCADE`. `axes` is JSONB.
- Chain: `0012_business_records` → `0013_business_suggestions` → `0014_business_positioning_maps`,
  the current sole alembic head (verified via the `scripts/e2e_run.sh` run below, which migrates a
  fresh `cofoundaz_e2e` through `0014` from zero).
- `app/db/models/business.py` — `BusinessSuggestion`, `BusinessPositioningMap`.
- `app/db/models/enums.py` — `SuggestionOp` (`canvas_update`/`record_create`/`record_update`/
  `record_delete`), `SuggestionStatus` (`pending`/`approved`/`rejected`).
- `CompetitorData` (`app/services/business/record_defs.py:29-37`) — extended with `map_x: float |
  None`, `map_y: float | None` (`Field(ge=0, le=1)`, both default `None`). **No migration for
  this** — it's two new keys inside the existing `business_records.data` JSONB column, not a
  schema change.

**Services**
- `app/services/business/suggestions.py` — `create_suggestion` (per-op target/payload validation
  + `base_version` capture for `canvas_update`), `list_suggestions` (status filter), `_current`
  (live diff-view read), `serialize_suggestion`, `_apply` (dispatches to the existing Slice 1/2
  write functions), `_require_pending`, `approve_suggestion`, `reject_suggestion`.
- `app/services/business/positioning.py` — `get_or_create_map` (lazy-create, SAVEPOINT-guarded
  race-safe insert, mirrors `get_or_create_canvas`), `validate_axes`, `update_axes`,
  `serialize_map`, `assemble_map` (joins the map's `axes` with every `competitor` record's
  `map_x`/`map_y`/`threat_level`).
- `app/schemas/business.py` — `SuggestionCreate` (`{op, target, payload, note}`),
  `PositioningMapSave` (`{axes}`).

**Endpoints** (all under `/api/v1/business-builder`, `app/api/v1/endpoints/business.py`;
registered before the Slice 2 `{kind}` catch-all, after the Slice 1 literal canvas paths — same
route-ordering discipline as Slice 2)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/business-builder/suggestions` | any active member | 201; per-op 404/422 at create time |
| GET | `/api/v1/business-builder/suggestions` | any active member | `?status=pending\|approved\|rejected`; unknown status → 404 |
| POST | `/api/v1/business-builder/suggestions/{id}/approve` | founder / team_member (editor) | applies via `_apply()`; 409 `SUGGESTION_NOT_PENDING` / 409 `CANVAS_VERSION_CONFLICT` / 404 (target gone) |
| POST | `/api/v1/business-builder/suggestions/{id}/reject` | founder / team_member (editor) | 409 `SUGGESTION_NOT_PENDING` on a non-pending suggestion |
| GET | `/api/v1/business-builder/positioning-map` | any active member | lazy-creates the axes row on first read |
| PUT | `/api/v1/business-builder/positioning-map` | founder / team_member (editor) | axes only — see the coordinate trap in How/the FE guide |

Competitor coordinates use the EXISTING Slice 2 endpoints (`POST`/`PUT /competitors`), not a new
route.

**Errors** — one new code (`SuggestionNotPending`, `app/core/errors.py:111-112`, 409
`SUGGESTION_NOT_PENDING`). Reuses `CanvasVersionConflict` (409, unchanged from Slice 1),
`NotFound` (404), `Forbidden` (403), `VALIDATION_ERROR` (422) — no other new error codes.

**Events** — `business.suggestion.created`, `business.suggestion.approved`,
`business.suggestion.rejected` (`{startup_id, suggestion_id, op}`), published from
`create_suggestion`/`approve_suggestion`/`reject_suggestion`. No consumer yet (same
enqueue-now/consume-later posture as every other event in this codebase pre-Module-03).
`business.artifact.completed` continues to fire from inside `save_canvas`/`create_record` when a
suggestion's approval crosses that completion threshold — suggestions don't suppress or duplicate
that event, since they run through the same functions.

**Tests**
- `tests/services/business/test_suggestions.py`, `test_suggestions_resolve.py` — per-op
  create-time validation (404/422), `_current`'s canvas_update/record_update/record_create
  branches, `_apply`'s dispatch per op, the pending→approved/rejected state machine, both 409s.
- `tests/services/business/test_positioning.py` — `get_or_create_map` race-safety, `validate_axes`,
  `assemble_map`'s competitor projection.
- `tests/api/test_business_suggestions.py` — the four create-suggestion 404/422 paths, list +
  status filter (incl. unknown-status 404), approve/reject 403 for a non-editor, both 409s over
  HTTP, cross-tenant 404.
- `tests/api/test_business_positioning.py` — GET lazy-create, PUT axes 422 on bad shape, PUT 403
  for a non-editor, map assembly with competitor coordinates.
- `e2e/test_business_builder.py::test_business_suggestions_journey` (new, this task) — a founder +
  an invited `business_consultant` in the SAME workspace; the consultant suggests all four ops; is
  forbidden from approving; the founder lists/approves/rejects, hitting both 409s live.
- `e2e/test_business_builder.py::test_business_positioning_map_journey` (new, this task) — default
  axes, PUT new axes, POST a competitor with coordinates, live proof of the coordinate-on-competitor
  trap (PUT /positioning-map with a `competitors` key is a silent no-op) and its fix (PUT
  /competitors/{id}).

## Verification

- **Unit suite: 824 passed** (`poetry run pytest`) — 32 of those are Slice 3-specific
  (`tests/api/test_business_suggestions.py`, `test_business_positioning.py`,
  `tests/services/business/test_suggestions.py`, `test_suggestions_resolve.py`,
  `test_positioning.py`), on top of the baseline this branch inherited from `develop` after PR
  #46 (Slice 2) merged.
- **Live E2E: 32 passed** (`scripts/e2e_run.sh`) — up from Slice 2's 30, +2 for this task's two new
  journeys. Full run: docker db+redis up, `alembic upgrade head` on a fresh `cofoundaz_e2e`
  (verified migrating cleanly through `0011_business_canvases` → `0012_business_records` →
  `0013_business_suggestions` → `0014_business_positioning_maps`), real uvicorn, all 32 tests green
  on the first run — **no server-only defect was found** (the brief flagged "a missing
  `db.commit()`" as a plausible failure mode; every write endpoint in `business.py` was checked and
  already calls `db.commit()` after its service call, consistent with Slice 1/2's pattern — no fix
  was needed).
  - `test_business_suggestions_journey`: founder onboards + invites a `business_consultant` (sent
    while onboarding is still a draft, same constraint as `e2e/test_roadmap.py`) → founder GETs
    `business_model` (lazy-create v1) → consultant suggests `canvas_update` (`base_version: 1`) →
    consultant gets `403 FORBIDDEN` approving it → founder lists `?status=pending`, sees it →
    founder approves (canvas → v2, `key_partners` filled) → GET canvas confirms → re-approving →
    `409 SUGGESTION_NOT_PENDING` → consultant suggests a second `canvas_update` (`base_version: 2`)
    → founder directly `PUT`s the canvas to v3 before resolving it → approving the second suggestion
    → `409 CANVAS_VERSION_CONFLICT`, suggestion confirmed still `pending` → founder rejects it →
    consultant suggests `record_create` (persona), `current` confirmed `null` → founder approves,
    persona confirmed created → consultant suggests `record_update` against that persona, `current`
    confirmed non-null (the existing record data) → founder approves, update confirmed applied →
    consultant suggests `record_delete` → founder approves, persona confirmed gone
    (`GET /personas` → `"records": []`).
  - `test_business_positioning_map_journey`: `GET /positioning-map` shows default Price/Quality axes
    + no competitors → `PUT` new axes → `POST /competitors` with `map_x`/`map_y` → `GET
    /positioning-map` shows the competitor with coordinates → `PUT /positioning-map` with an
    extraneous `competitors` key confirmed to be a silent no-op (coordinates unchanged on re-GET) →
    `PUT /competitors/{id}` confirmed as the correct way to move coordinates → `GET
    /positioning-map` reflects the new coordinates.
  - 25 new captures to `e2e/_captures/business/{suggestions_*,positioning_*}.json` — every one is
    the verbatim source for `docs/fe-integration-guide-business-builder-suggestions.md`, including
    both 409 bodies and the 403 body (this task's guide has zero source-derived, unexercised error
    rows, unlike Slice 1/2's guide).
- `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app` —
  all clean.
- Migration round-trip verified via the `scripts/e2e_run.sh` run above (fresh `cofoundaz_e2e`
  migrated `0012_business_records` → `0014_business_positioning_maps` from zero); `alembic heads`
  confirms `0014_business_positioning_maps` is the sole head.

## Operate / roll back

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `scripts/e2e_run.sh`
  flow.
- **Rollback:** `alembic downgrade -1` twice from `0014_business_positioning_maps` — first drops
  `ix_business_positioning_maps_startup_id` then `business_positioning_maps`
  (`0014`→`0013`), then `ix_business_suggestions_startup_status`,
  `ix_business_suggestions_startup_id`, `ix_business_suggestions_author_id`, then
  `business_suggestions` (`0013`→`0012`). **Both are lossy** — any suggestion or positioning-map
  data written while these migrations were applied is destroyed on downgrade, same as every other
  brand-new-table migration in this project. Rolling back the migrations without also reverting the
  suggestion/positioning-map endpoint registrations in `app/api/v1/endpoints/business.py` would make
  those six routes fail on now-missing tables — roll back the migrations and the endpoint additions
  together, or accept that the suggestion/positioning-map surface will start raising until the code
  is reverted too. `CompetitorData.map_x`/`map_y` need NO migration to roll back — they're schema
  fields on a JSONB column; reverting `record_defs.py`'s `CompetitorData` alone (no DB change)
  removes them from validation, and any already-stored `map_x`/`map_y` keys simply become inert
  extra JSONB data (harmless, since `data` is read as a raw dict by `assemble_map`, not
  re-validated against the schema on every read).

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Reject-reason field.** `reject_suggestion` takes no reason/comment — a founder can reject, but
  there's no structured place to say why, so the consultant only learns "it was rejected", not what
  to change. The PRD doesn't call this out explicitly; noted here as a likely quick follow-up
  (`BusinessSuggestion` would need a nullable `reject_reason: Text` column + a request body field on
  `POST .../reject`) rather than a re-scope.
- **AI Business Plan Generator (PRD §08.11) is still fully out of scope.** `POST
  /business-builder/plan/generate`, the `business_plans` entity, and the `business.plan.generated`
  event from the PRD's Module 08 spec are not part of Slice 3 (or any slice shipped so far) — this
  is a deliberate dependency block, not a gap in this slice's scope: the plan generator needs a real
  AI generation pipeline (**Module 03 — AI Co-Founder**, not started) to actually draft plan
  sections from the founder's completed canvases/records, and needs a real document store to write
  the generated plan into (**Module 18 — Documents & Templates**, not started — the PRD's own
  CTA for a generated plan is "Open document" → a Module 18 doc). Module 08 is feature-complete for
  every Slice 1–3 item EXCEPT this one PRD sub-screen (§08.11), which cannot ship correctly until
  both dependencies exist.
- **No suggestion diff UI contract beyond `current`/`payload`.** The API gives the FE the raw
  before (`current`) and proposed (`payload`) states and leaves rendering a human-readable diff
  entirely to the client — there's no server-computed "what specifically changed" summary (e.g.
  "added 1 item to Key Partners"). Fine for now given the small block/field counts involved; would
  be worth revisiting if a canvas grows large lists where a raw before/after `blocks` object is hard
  to scan.
- **`create_suggestion`'s 404 checks for `record_update`/`record_delete` use the suggester's own
  membership, which is correct tenant isolation but means a consultant gets the SAME generic 404 for
  "record doesn't exist" and "record belongs to a different tenant"** — consistent with every other
  `_record()` lookup in this codebase (Slice 2's SOP notes the identical tradeoff), not a new gap
  introduced here.
- **No real-time notification on `business.suggestion.created`/`approved`/`rejected`.** The PRD
  calls for a founder notification ("{Consultant} suggested changes to {artifact}") on BC suggest —
  the event fires, but there is no subscriber wired to turn it into an actual notification yet (same
  "enqueue now, consume later" posture as every event in this codebase pre-Module-03/whatever module
  ends up owning notifications).
