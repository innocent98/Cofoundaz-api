# SOP — Business Builder Typed Artifacts (Module 08, Slice 2)

**What shipped** — the second Business Builder surface: four typed-artifact record kinds
(`persona`, `revenue_stream`, `competitor`, `pricing`), each a per-founder collection of
Pydantic-validated records under a uniform `{kind}` CRUD route, layered on top of Slice 1's
canvas core (`docs/sop/2026-09-01-business-builder-canvas.md`) rather than replacing it. `GET
/business-builder/{kind}` lists a startup's records for that kind plus a `fields` descriptor (incl.
enum `choices`) the FE renders a create/edit form from; `POST /{kind}` creates one (201, 422 on bad
`data`); `PUT /{kind}/{id}` full-replace-updates one (404 cross-tenant/unknown); `DELETE
/{kind}/{id}` removes one; `POST /{kind}/ai-fill` enqueues a deferred AI-fill job, same seam as
Slice 1's canvas ai-fill. `GET /business-builder/overview` now returns 9 rows, not 5 — the
original 5 canvas-type rows plus 4 new record-kind rows (`status`/`completion_pct` derived from
`count >= 1`, not from a filled-blocks ratio). One new table (`business_records`, migration
`0012_business_records`).

Commits (branch `feat/business-builder-records`): `ba1bb2d`..`4095e33` (Tasks 1–5) + this task's
E2E/SOP/FE-guide/checklist commit (Task 6 — final task of the 6-task plan,
`.superpowers/sdd/2026-09-04-business-builder-records/`).

## Why

Slice 1 gave founders five generic strategy canvases of loosely-typed text/list blocks. But some
of the things a founder needs to capture in Business Builder aren't a block of free text — they're
a *collection of structured records*: a founder has several customer personas, several competitors,
several pricing tiers, each with fields that should be validated (a persona needs a `name`; a
competitor's `threat_level` should be one of a fixed set, not any string a founder happens to type).
Slice 2's job is exactly that: typed, per-kind CRUD over founder-created records, still deliberately
deferring the *real* AI-fill work and any suggestion/plan-generation logic to later slices/modules —
same "core mechanic now, AI seam later" split Slice 1 made for canvases.

## How

**A single generic `business_records` table + an in-code Pydantic schema registry, not four
per-kind tables.** `BusinessRecord` (`app/db/models/business.py`) is one table with a `kind` column
(`RecordKind` enum) and a JSONB `data` column — not four per-kind tables and not four separate
response models. The per-kind shape (which fields exist, their types, their defaults, which ones
are enums) lives entirely in `RECORD_SCHEMAS` (`app/services/business/record_defs.py`), a
`dict[RecordKind, type[BaseModel]]` of four Pydantic v2 models (`PersonaData`, `RevenueStreamData`,
`CompetitorData`, `PricingData`). This is the exact same "generic table + code-level registry"
tradeoff Slice 1 made for `CANVAS_BLOCKS` (see that SOP's "How" section) — adding a fifth record
kind later is a new Pydantic model + a `RECORD_SCHEMAS`/`RecordKind` entry, not a migration.

**Validation is real Pydantic model validation, not a hand-rolled per-field checker.** Unlike
Slice 1's `validate_blocks` (which only checks `"text"` vs `"list"` shape), Slice 2's `validate()`
(`app/services/business/records.py:15`) instantiates the kind's actual `RECORD_SCHEMAS[kind]` model
against the incoming `data` dict and catches `pydantic.ValidationError`, translating every error
into the standard `{field, message}` `field_errors` shape on a 422 `AppError`. This gets real type
coercion, defaults, nested-model validation (`PricingData.tiers: list[PricingTier]`), and enum
membership checking (`ThreatLevel`, `PricingModelType`) for free from Pydantic — no bespoke
"is this a valid threat level" check to hand-write or keep in sync. `model_config =
ConfigDict(extra="forbid")` on every schema's shared `_Base` means an unknown field in `data` also
422s, not silently getting dropped.

**`fields()` exposes `choices` specifically so the FE never hardcodes an enum's valid values.**
`fields(kind)` (`record_defs.py:57`) walks `RECORD_SCHEMAS[kind].model_fields` and, for any field
whose annotation is an `enum.Enum` subclass, includes `choices: [member.value, ...]` — `null` for
every non-enum field. `threat_level` → `["low", "medium", "high"]`; `model_type` → the 5
`PricingModelType` values. This mirrors Slice 1's `block_defs` contract (serve the shape, don't
make the FE hardcode it) but one level deeper — Slice 1 only needed to tell the FE `"text"` vs
`"list"`; Slice 2's fields can be a closed enum, so the descriptor needs to carry the actual
allowed values for the FE to build a dropdown from. **Caveat surfaced in the FE guide:** `fields()`'s
`type` string is a raw `str(annotation)` (e.g. `"<class 'str'>"`, `"<enum 'ThreatLevel'>"`,
`"list[app.services.business.record_defs.PricingTier]"`) — a Python-internal repr, not a stable
wire contract. Only `key`, `required`, and `choices` are meant to be parsed by the FE; `type` is a
human-readable hint, confirmed leaking Python internals in the live capture
(`e2e/_captures/business/competitor_list.json`).

**`create_record` assigns position by counting existing rows — this is the one piece of Slice 2
that is deliberately *not* race-safe, unlike Slice 1's `get_or_create_canvas`.** `create_record`
(`records.py:40`) does `count = ...count(); row = BusinessRecord(..., position=count)`. Two
concurrent `POST`s for the same `(startup_id, kind)` can both read the same `count` and both insert
at the same `position` — there is no unique constraint on `(startup_id, kind, position)` to catch
it (see What's involved → migration). This was accepted for Slice 2 because a founder adding
personas one at a time from a single session is not the double-tab-race scenario Slice 1's
`get_or_create_canvas` was hardened against (a *lazy-create-on-first-read* that any of N members
could trigger simultaneously); explicit `POST`s from a form are a much lower-probability race. Flagged
here and in Follow-ups as a real, not-yet-closed gap — not an oversight being hidden.

**`update_record` is full-replace, exactly like Slice 1's `save_canvas`.** `update_record`
(`records.py:54`) does `record.data = validate(record.kind, data)` — it does not merge; it
re-validates the *entire* incoming `data` against the schema (which applies that schema's own
defaults for any omitted field) and replaces the row's `data` wholesale.
`tests/services/business/test_records.py::test_update_full_replaces_data` pins this: updating a
persona's `name` alone resets its `quote` back to `""`, not to whatever it was before. Same FE
contract as Slice 1's canvas PUT: always resend the *complete* `data` object, never a diff.

**`business.artifact.completed` fires once, on the first record of a kind — payload shape differs
from Slice 1's canvas event.** `create_record` publishes `("business.artifact.completed",
{"startup_id": ..., "artifact": kind.value})` only when `count == 0` (i.e., this is the kind's
first record) — mirroring Slice 1's "guard on the transition, not the action" pattern, but the
transition here is *count 0 → 1*, not *not-complete → complete* completion-percentage crossing.
**Note for a future consumer:** the payload key is `artifact` (Slice 2) vs `canvas_type` (Slice 1)
— same event name, different payload shape depending on which kind of Business Builder object
completed. A future subscriber needs to branch on which key is present, or the two payload shapes
should be reconciled before Module 03 wires a real consumer.

**Overview's record rows reuse the canvas rows' `{type, label, status, completion_pct}` shape but
compute `status`/`completion_pct` from `count`, not from a blocks-filled ratio.** `overview()`
(`app/services/business/service.py:133`) now does one `GROUP BY kind` count query across
`business_records` after building the 5 canvas rows, then appends one row per `RecordKind`:
`status = "complete" if count >= 1 else "start"`, `completion_pct = 100 if count >= 1 else 0`. There
is no `"continue"` state for a record-kind row (unlike a canvas row, which can be partially filled)
— a record kind is binary: the founder has added at least one, or none. This is a coarser
completion model than canvases' `filled_blocks/total_blocks` ratio, chosen because "how many
personas is enough" has no natural denominator the way "9 canvas blocks" does; `count` is exposed
raw so the FE can show "3 personas" rather than a meaningless percentage past the first one.

**`ai-fill` for a record kind writes a job row and nothing else, same seam as Slice 1.**
`ai_fill_kind` (`app/api/v1/endpoints/business.py:152`) resolves the startup, enqueues
`job_dispatcher.enqueue(type=f"business.{rk.value}.ai_fill", payload={startup_id, kind}, ...)`,
commits, and returns 202 `{job_id, status: "queued"}` — no `BusinessRecord` row touched.
`tests/api/test_business_records.py::test_ai_fill_enqueues_job_writes_no_record` asserts the table
stays empty. The FE polls the same existing `GET /jobs/{id}` used by every other deferred-job seam
in this codebase.

**Routing: the `{kind}` catch-all is registered after every literal path.** `app/api/v1/endpoints/
business.py` declares `/overview`, `/canvases/{type}`, and `/canvases/{type}/ai-fill` *before*
`/{kind}`, `/{kind}/ai-fill`, `/{kind}/{record_id}` — FastAPI matches routes in registration order,
so a literal `/overview` or `/canvases/...` request must never fall through to the generic `{kind}`
handler and 404 via `_parse_kind`. The full Slice 1 canvas test suite (`tests/api/
test_business_canvases.py`, `e2e/test_business_builder.py::test_business_builder_journey`) was
re-run at every task in this plan specifically to catch any such shadowing regression — none
occurred.

## What's involved

**Data model / migration**
- `alembic/versions/0012_business_records.py` — one new table, no lock on any existing table
  (autogenerated from the Task 1 ORM model; only revision id/`down_revision`/docstring hand-edited).
  - `business_records` (+ standalone index `ix_business_records_startup_id`, + composite index
    `ix_business_records_startup_kind` on `(startup_id, kind, position)` — **not** a unique
    constraint, see the concurrency caveat above and in Follow-ups). `startup_id` FK
    `ON DELETE CASCADE`; `kind` is a `native_enum=False` VARCHAR(20), matching this codebase's
    standard StrEnum-column convention; `data` is JSONB; `position` is a plain `Integer`.
- `app/db/models/business.py` — `BusinessRecord`.
- `app/db/models/enums.py` — `RecordKind` (`persona`/`revenue_stream`/`competitor`/`pricing`),
  `ThreatLevel` (`low`/`medium`/`high`), `PricingModelType` (`subscription`/`one_time`/`usage`/
  `freemium`/`tiered`).
- Chains directly off `0011_business_canvases` — `0012_business_records` is the current, sole
  alembic head (verified via the `make e2e` run below, which migrates a fresh `cofoundaz_e2e`
  through `0012`).

**Schemas / registry** — `app/services/business/record_defs.py`
- `PersonaData`, `RevenueStreamData`, `CompetitorData`, `PricingData` (+ nested `PricingTier`) —
  each `_Base(BaseModel)` with `model_config = ConfigDict(extra="forbid")`.
- `RECORD_SCHEMAS: dict[RecordKind, type[BaseModel]]` — the kind → schema registry.
- `fields(kind) -> list[dict]` — `{key, required, type, choices}` per field; `choices` is the
  enum's member values for an `enum.Enum`-typed field, else `None`.
- `app/schemas/business.py` — `RecordCreate` (`{data: dict}`), reused for both `POST` and `PUT`
  bodies (same request shape, `app/api/v1/endpoints/business.py:140,175`).

**Service** — `app/services/business/records.py`
- `validate(kind, data) -> dict` — instantiates `RECORD_SCHEMAS[kind]`, converts
  `pydantic.ValidationError` → 422 `AppError` with `field_errors`.
- `list_records(db, startup, kind) -> list[BusinessRecord]` — ordered by `position asc`.
- `create_record(db, startup, kind, data) -> BusinessRecord` — validate → count-based `position` →
  insert → `business.artifact.completed` on the kind's first record.
- `update_record(db, record, data) -> BusinessRecord` — validate → full-replace `record.data`.
- `delete_record(db, record) -> None`.
- `serialize_record(record) -> dict` — `{id, kind, data, position}`.
- `_record(db, membership, kind, record_id) -> BusinessRecord` — tenant-scoped lookup (`startup_id
  = membership.startup_id`), 404 on miss (unknown id **or** cross-tenant id — same code path,
  can't distinguish the two from the response, which is the deliberate tenant-isolation behavior
  every other module in this codebase uses).
- `app/services/business/service.py::overview()` — extended (Task 2) to append the 4 record-kind
  rows after the 5 canvas-type rows, via one `GROUP BY kind` count query.

**Endpoints** (all under `/api/v1/business-builder`, `app/api/v1/endpoints/business.py`; the
`{kind}` routes registered *after* the Slice 1 literal-path routes — see the routing note above)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/business-builder/{kind}` | any active member | `{records: [...], fields: [...]}`; unknown `kind` → 404 |
| POST | `/api/v1/business-builder/{kind}` | founder / team_member (editor) | 201; bad `data` → 422 `VALIDATION_ERROR` |
| PUT | `/api/v1/business-builder/{kind}/{record_id}` | founder / team_member (editor) | full-replace `data`; unknown/cross-tenant id → 404 |
| DELETE | `/api/v1/business-builder/{kind}/{record_id}` | founder / team_member (editor) | `{deleted: true}` |
| POST | `/api/v1/business-builder/{kind}/ai-fill` | founder / team_member (editor) | 202, enqueues `business.{kind}.ai_fill` job, writes no record |

`{kind}` path segments: `personas` → `RecordKind.persona`, `revenue-streams` →
`RecordKind.revenue_stream`, `competitors` → `RecordKind.competitor`, `pricing` →
`RecordKind.pricing` (`_KIND_PATHS`, `business.py:105`) — note the plural/hyphenated URL segments
map to singular/underscored enum values; `_parse_kind()` raises `NotFound()` on any other segment.

**Errors** — no new error codes. Reuses `VALIDATION_ERROR` (422, bad `data` against the kind's
schema), `NOT_FOUND` (404, unknown `{kind}` segment or unknown/cross-tenant `{record_id}`), and
`FORBIDDEN` (403, mentor attempting a write) — same three Slice 1 already introduced/reused.

**Events** — `business.artifact.completed` (`{startup_id, artifact}`), published on a kind's
`count 0 -> 1` transition only (payload key `artifact`, distinct from Slice 1's `canvas_type` key
on the same event name — see the How section). No consumer yet.

**Tests**
- `tests/services/business/test_record_defs.py` — schema registry completeness, per-kind defaults/
  required fields, enum validation (`threat_level`, `model_type`), nested `PricingTier` validation,
  `fields()`'s `choices` exposure.
- `tests/services/business/test_records.py` — `validate` rejects bad data/bad enum choices,
  `create_record` position-ordering, `update_record` full-replace semantics, `delete_record`,
  the `business.artifact.completed` first-record-only event, `overview()`'s record rows.
- `tests/api/test_business_records.py` — list+fields, create 201, bad-data 422, unknown-kind 404,
  mentor 403, PUT full-replace, delete, PUT against a random (not a real second tenant's) id 404,
  ai-fill enqueues job + writes no record, ai-fill mentor 403.
- `e2e/test_business_builder.py`:
  - `test_business_builder_journey` (Slice 1, updated this task) — its two `GET /overview`
    assertions were adjusted from "exactly the 5 canvas types" to "the 5 canvas types plus the 4
    record kinds" now that `overview()` returns 9 rows; the canvas-specific assertions
    (`business_model`'s `filled_blocks`/`status` transition) are unchanged and still pass.
  - `test_business_builder_records_journey` (new, this task) — the full records walk described
    in Verification below.

## Verification

- **Unit suite: 790 passed** (`poetry run pytest -q`) — 43 of those are business-builder-specific
  across both slices (20 Slice 1 + 23 Slice 2: `tests/services/business/test_record_defs.py`,
  `test_records.py`, `tests/api/test_business_records.py`), unchanged from the 767 baseline at the
  end of Slice 1 plus this slice's additions (767 + 23 = 790).
- **Live E2E: 30 passed** (`COMPOSE_PROJECT_NAME=cofoundaz-api make e2e`, this task, up from
  Slice 1's 29) — `test_business_builder_records_journey`: a founder onboards → `POST /personas`
  creates a persona (201, `position: 0`) → `GET /personas` lists it back ordered, with `fields`
  including `name` → `PUT /personas/{id}` full-replace-updates it (omitted `quote`/`demographics`
  reset to `""`, matching the pinned unit-test behavior) → `GET /overview` shows the `persona` row
  `status == "complete"`, `count == 1`, the other 3 record kinds still `"start"`/`count 0` →
  `POST /competitors` (with `threat_level: "high"`) and `GET /competitors` confirm `fields`
  surfaces `threat_level`'s `choices` as `["low", "medium", "high"]` live → `POST /pricing` (with
  `model_type: "tiered"` + 2 `tiers`) and `GET /pricing` confirm `model_type`'s `choices` are the
  5 `PricingModelType` values live → `POST /personas/ai-fill` returns 202 + `{job_id, status:
  "queued"}` → `GET /jobs/{id}` shows the same job, still `"queued"`. The pre-existing
  `test_business_builder_journey` (Slice 1) was re-run and re-verified passing after its `/overview`
  assertions were widened for the new record rows. Every response captured verbatim to
  `e2e/_captures/business/{record_create,record_list,record_update,overview_records,
  competitor_create,competitor_list,pricing_create,pricing_list,record_ai_fill,
  record_ai_fill_job}.json` (10 new captures) — plus the 4 pre-existing Slice 1 captures that
  necessarily changed shape this run (`overview_empty.json`/`overview_after.json` now carry the 4
  record rows; `ai_fill.json`/`ai_fill_job.json` carry a fresh job id from this run) — all are the
  source for `docs/fe-integration-guide-business-builder.md`.
- `poetry run black --check app tests e2e`, `poetry run isort --check-only app tests e2e`,
  `poetry run ruff check .`, `poetry run mypy app` — all clean.
- Migration verified via the `make e2e` run above (fresh `cofoundaz_e2e` DB migrated
  `0011_business_canvases -> 0012_business_records`); `alembic heads` confirms
  `0012_business_records` is the sole head.

## Operate / roll back

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `make e2e` flow.
- **Rollback:** `alembic downgrade -1` from `0012_business_records` drops
  `ix_business_records_startup_kind`, then `ix_business_records_startup_id`, then
  `business_records`. **Lossy** — any record data written while `0012` was applied is destroyed on
  downgrade, same as every other brand-new-table migration in this project. Rolling back the
  migration without also reverting the `{kind}` routes' registration in
  `app/api/v1/endpoints/business.py` would make all five new endpoints fail on the now-missing
  table, and would also break `GET /overview` (its record-rows `GROUP BY` query would hit a
  dropped table) — roll back the migration, the endpoint additions, and the `overview()` extension
  together, or accept that overview and the record endpoints will start raising until the code is
  reverted too.

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Slice 3 — suggestions + plan.** Still no suggestion engine, no cross-artifact consistency
  checking (e.g. flagging a persona whose frustrations don't map to any pricing tier), and no
  plan-generation from a completed set of records — Slice 2 only gets the founder to a filled-in
  set of typed records, not what happens next with them.
- **Real `business.{kind}.ai_fill` worker → Module 03 (AI Co-Founder).** Every record-kind ai-fill
  job is enqueued and persisted as `queued` and stays that way — no worker drains it, same
  not-yet-consumed seam as Slice 1's `business.canvas.ai_fill` and every other deferred job in this
  codebase. The FE guide marks this "derived/known-deferred", not a bug.
- **`positioning-map`-style canvases and any Slice-2-adjacent artifact types not in this batch**
  (e.g. a dedicated competitor positioning map, richer revenue-stream modeling) remain out of scope
  — the 4 kinds shipped here (`persona`, `revenue_stream`, `competitor`, `pricing`) are the set the
  Slice 2 design spec scoped; a 5th kind is a schema + registry entry away, not a redesign.
- **Module 12 (Revenue) sync.** `RevenueStreamData` here is a founder's own planning input, not
  wired to any real revenue tracking Module 12 will eventually own — no reconciliation between "the
  revenue streams a founder listed in Business Builder" and "what Module 12 actually measures"
  exists yet. Revisit once Module 12 ships.
- **No reordering endpoint.** `position` is set once at creation (append-only, by count) and never
  otherwise mutated — there is no `PATCH .../{id}/reorder` or drag-reorder endpoint yet, even though
  the FE-facing shape (`position` on every record) anticipates one. `list_records` always returns
  `position asc`, so the ordering is stable and readable today; only *changing* it is unimplemented.
- **`create_record`'s position assignment is not race-safe** (see How section) — two concurrent
  `POST`s for the same `(startup_id, kind)` can compute the same `count` and insert duplicate
  `position` values; there is no unique constraint on `(startup_id, kind, position)` to catch it
  (unlike Slice 1's `get_or_create_canvas`, which is hardened against exactly this shape of race via
  a `db.begin_nested()` SAVEPOINT + re-select). Revisit if concurrent-create collisions turn out to
  matter in practice — the fix would mirror `get_or_create_canvas`'s pattern, or move position
  assignment to a DB-side sequence/`MAX(position)+1` under a row lock.
- **`test_put_cross_tenant_404` is not yet a *genuine* cross-tenant test.** The current test
  (`tests/api/test_business_records.py::test_put_cross_tenant_404`) asserts 404 on a `PUT` against a
  freshly-generated random UUID that was never inserted at all — it proves "unknown id → 404" but
  does not prove "a *real* record belonging to a *different* startup → 404" (i.e., that
  `_record()`'s `startup_id = membership.startup_id` filter actually blocks a cross-tenant read, not
  just a nonexistent one). Same gap exists for `DELETE` — there is no cross-tenant `DELETE` test at
  all yet. Both are believed correct by code inspection (`_record()` filters by
  `membership.startup_id` unconditionally), but "believed correct" is exactly the gap a test should
  close. Recommended follow-up: add `test_put_cross_tenant_404`/`test_delete_cross_tenant_404`
  variants that create a *second* startup + membership, create a real record under startup A, and
  assert startup B's membership gets 404 on `PUT`/`DELETE` against that real id.
- **JSONB key order is not preserved** for `data`, same caveat as Slice 1's `blocks` — observed live
  in the captures (e.g. `record_create.json`'s `data` keys come back in a different order than they
  were sent). Not a bug; the FE guide repeats Slice 1's guidance not to rely on it.
