# SOP — Typed Records AI Fill (Module 03 Slice 3)

**What shipped** — the third slice of Module 03 (AI Co-Founder): a real worker for
`business.{kind}.ai_fill` (kind ∈ persona/revenue_stream/competitor/pricing), the job Module 08
Slice 2 has enqueued on `POST /business-builder/{kind}/ai-fill` since it shipped, with no handler
ever claiming it. That job now drafts up to 3 records for a kind that's completely EMPTY, via one
`complete_json` call constrained to a strict per-kind JSON Schema derived from the existing
`RECORD_SCHEMAS` Pydantic registry, validating (and skipping, not failing) each drafted record
through the same `create_record` path a manual `POST /{kind}` already uses. No new API surface, no
new route, no migration — the same "reuse an existing enqueue point, add the missing handler"
pattern Module 03 Slice 2 used for canvas ai-fill.

Commits (branch `feat/records-ai-fill`, off `develop` @ `c8debfc`):
`e93c2b7` (design) → `6cfbd15` (implementation plan,
`.superpowers/sdd/2026-09-19-records-ai-fill/`) → `5da90d8` (Task 1 — recursive `StubLLMClient`
stub, `record_json_schema(kind)` strict per kind, `build_record_fill_messages`) → `5d59ac1`
(Task 2 — `handle_record_ai_fill`, registered for all 4 `business.{kind}.ai_fill` job types) →
**this commit** (Task 3, final — live e2e + FE guide + SOP + checklist reconcile).

## Why

Module 03 Slice 2's own SOP Follow-ups called this out explicitly: **`business.{kind}.ai_fill` was
the only ai-fill job left unconsumed** once Slice 2 shipped the canvas worker and §08.11 (AI
Business Plan Generator) shipped on the free-text `complete()` seam without needing it. Every
`POST /business-builder/{kind}/ai-fill` call in production has been enqueuing a job that fails
terminally under the runner's own "no handler" dispatch (`app/worker/runner.py`) since Module 08
Slice 2 shipped that trigger endpoint. Closing this gap was the last piece needed before Module 03
can be considered feature-complete against its own currently-scoped consumers (mission reason
lines, health recommendations, dashboard briefing, and the onboarding AI panel remain separately
deferred — see Follow-ups).

Records ai-fill needed a **different schema variant** than canvas ai-fill, not a copy-paste:
`RECORD_SCHEMAS` is a Pydantic v2 registry (`PersonaData`/`RevenueStreamData`/`CompetitorData`/
`PricingData`), not the dataclass `BlockDef` registry `CANVAS_BLOCKS` used, and the target shape is
"up to 3 records of one kind" (an array), not "one value per fixed block key" (a flat map). That
shape difference is also why the stub LLM client needed a real recursion fix in Task 1: Slice 2's
`StubLLMClient._stub_value` only handled one level of `{type: object}` → `{type: array | string |
number}` properties, enough for a flat canvas schema but not enough for `record_json_schema`'s
nested `{records: [{name, goals: [...], ...}]}` shape.

## How

**`record_json_schema(kind)` is strict per kind, hand-built from the same shape `RECORD_SCHEMAS`
validates against** (`app/services/business/record_defs.py::_record_item_schema` +
`record_json_schema`). Each kind gets `{"type": "object", "properties": {"records": {"type":
"array", "maxItems": 3, "items": <kind's item schema>}}, "required": ["records"],
"additionalProperties": false}`. The per-kind item schema mirrors each Pydantic model's fields
one-for-one, with two deliberate exceptions: `CompetitorData.map_x`/`map_y` (the UI's positioning
map coordinates) are **intentionally omitted** — the AI has no business placing a competitor on the
founder's visual map — and enum fields (`threat_level`, `model_type`) become `{"type": "string",
"enum": [...]}` so the model is constrained to a valid enum value, never free text that would fail
Pydantic validation downstream.

**The stub client's recursion had to grow up, not just get a new caller.** Slice 2's
`StubLLMClient._stub_value(node, key)` handled `object`/`array`/`number`/`string` at exactly one
level — sufficient for canvas schemas (`{block_key: string | array-of-string}`, no nesting). Records
need `{records: [{name: string, goals: [string], ...}]}` — an array of objects, each with its own
mix of string/array/enum fields. Task 1 made `_stub_value` genuinely recursive: an `object` node
recurses into each property with that property's own key (so `record["name"]` becomes `"[stub-llm]
name"`, not `"[stub-llm] records"`), an `array` node recurses into its `items` schema and wraps in a
single-item list, `enum` short-circuits to the first enum value (so `threat_level` always comes back
valid). **Existing canvas behavior is unchanged** — a flat schema recurses exactly once and produces
the same output as before; `test_stub_complete_json_still_flat_for_canvas_shape` pins this
regression directly.

**Fill-empties is a whole-kind gate, not a per-record augment.** `handle_record_ai_fill`
(`app/worker/handlers/ai.py`) checks `db.query(BusinessRecord).filter_by(startup_id=..., kind=kind)
.count()` before doing anything else — if the kind already has ≥1 record, the handler returns
immediately, no LLM call, no write. This is a coarser gate than canvas ai-fill's per-block
`empty_keys` check by design (see Follow-ups for why a per-record top-up mode is out of scope for
this slice): records don't have a natural "slot" the way canvas blocks do (there's no `key` a
drafted persona could collide with an existing one on), so "the kind has content" is the simplest
safe boundary between "nothing here yet, safe to draft" and "the founder already started, don't
touch it."

**Validate-and-skip, not validate-and-fail.** For each of up to 3 records in the LLM's `records`
array, the handler calls the existing `create_record(db, startup, kind, rec)` — the exact function a
manual `POST /{kind}` uses, so a drafted record gets the exact same Pydantic validation (`extra=
"forbid"`, required fields, enum membership) as anything a founder types by hand. A record that
fails validation is caught (`except AppError: continue`) and skipped rather than aborting the whole
job — one malformed record out of 3 shouldn't cost the founder the other 2 good ones. `create_record`
also fires `business.artifact.completed` on the kind's first record exactly as it always has (no new
event, no new notification category needed) — Slice 2's canvas guide's same-shaped promise.

**No commit, fail-loud on LLM error, no partial write.** Same convention as every handler in
`app/worker/handlers/ai.py`: `db.flush()` only, the runner (`app/worker/runner.py::run_once`) owns
the transaction/commit inside its `db.begin_nested()` savepoint. If `complete_json` itself raises
(bad key, non-2xx, unparseable JSON, non-dict result), the handler doesn't catch it — it propagates
out, nothing is created, the runner's existing retry/backoff (`WORKER_MAX_ATTEMPTS`) takes over.

**Data minimization — no PII in the prompt.** `build_record_fill_messages` (`app/services/business/
ai_fill.py`, extending Slice 2's canvas prompt builder in the same file) sends only the startup's
name, industry, stage, and the kind's label — never founder names, emails, or existing record data.
Pinned by `test_build_record_fill_messages_pii_free`.

## What's involved

**No migration.** `BusinessRecord` (Module 08 Slice 2's `0012_business_records` migration) is reused
as-is — this slice only changes what a worker writes into existing rows via the existing
`create_record` path. `poetry run alembic heads` returns exactly one head (`0026_business_plans`),
unchanged from `develop`.

**Stub + schema (Task 1, `5da90d8`)**
- `app/platform/llm.py::StubLLMClient._stub_value` — made genuinely recursive (object → recurse per
  property key, array → recurse into `items` and wrap, enum → first member), flat canvas shape
  behavior preserved.
- `app/services/business/record_defs.py` — `_record_item_schema(kind)` / `record_json_schema(kind)`,
  strict per kind, `map_x`/`map_y` intentionally omitted from `competitor`.
- `app/services/business/ai_fill.py::build_record_fill_messages(kind, *, name, industry, stage) ->
  list[LLMMessage]` — PII-free prompt.

**Worker (Task 2, `5d59ac1`)**
- `app/worker/handlers/ai.py::handle_record_ai_fill(db, job)` — count-gate, `complete_json` call,
  validate-and-skip loop (≤3 records), `db.flush()`.
- Registered for all 4 kinds: `for _kind in RecordKind: register_handler(f"business.{_kind.value}
  .ai_fill", handle_record_ai_fill)` — the exact job types `POST /{kind}/ai-fill` has enqueued since
  Module 08 Slice 2, no enqueue-side change needed.

**This task (Task 3)**
- `e2e/test_records_ai_fill.py`, new — see Verification below.
- `docs/fe-integration-guide-ai-canvas-fill.md` — extended with a new §5 "Records ai-fill" section
  (the doc now covers both Slice 2 and Slice 3's ai-fill triggers; §1–§4 unchanged).
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 03 gets a Slice 3 entry; Module 08's
  `business.{kind}.ai_fill` "enqueue-only, no worker" deferral lines updated to reflect the worker
  now shipping.
- Pre-existing formatting drift fixed as part of this task's full CI reproduction (mechanical only,
  no logic change — same "final task cleans up the slice's drift" precedent Slice 2's own Task 3
  SOP recorded): `black` reformatted `app/platform/llm.py`, `app/services/business/record_defs.py`,
  `tests/platform/test_llm.py`, `tests/services/business/test_records_ai_fill.py`,
  `tests/worker/test_records_ai_fill_handler.py` (all left unformatted by Tasks 1–2, which only ran
  `black`/`ruff` against their own touched files, not the whole-repo check this task runs).

**Errors / API surface** — none new. `POST /{kind}/ai-fill` (Module 08 Slice 2) already returned
`202 {job_id, status}` and already had its `_editor` role gate / `404` unknown-kind handling; this
slice only makes the job it enqueues actually do something. The only externally visible change is
that a kind's record list now populates asynchronously after a successful ai-fill on a previously
empty kind — see the FE guide §5 for the exact mechanics and the field-nesting trap (`data.records`,
not `data` directly).

## Verification

**Per-task unit verification (Tasks 1–2, already green before this task):**
- `tests/platform/test_llm.py` — 16 passed (pre-existing `complete`/`complete_json`/canvas-shape
  tests unchanged + 2 new: `test_stub_complete_json_recurses_nested_objects_and_enums`,
  `test_stub_complete_json_still_flat_for_canvas_shape` — the explicit regression pin).
- `tests/services/business/test_records_ai_fill.py` — 3 passed: schema strictness/shape per kind
  (`map_x`/`map_y` omitted), enum constraints, prompt PII-free.
- `tests/worker/test_records_ai_fill_handler.py` — 4 passed: `test_ai_fill_populates_empty_kind`,
  `test_ai_fill_noop_when_kind_not_empty`, `test_ai_fill_noop_when_startup_missing`,
  `test_ai_fill_fails_loud_on_llm_error`.

**Task 3 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests e2e` | ✅ pass (5 pre-existing files reformatted first — see "What's involved") |
| Import order | `poetry run isort --check-only app tests e2e` | ✅ pass |
| Lint | `poetry run ruff check app tests e2e` | ✅ pass |
| Types | `poetry run mypy app` | ✅ pass — no issues in 161 source files |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ pass — 9.90/10 (no new findings in AI/records code) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ pass, 0 findings (only pre-existing informational nosec warnings) |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ pass — **1266 passed, 97.42% coverage** (≥ 95% floor) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0026_business_plans (head)`, unchanged from `develop` |
| Live E2E | `./scripts/e2e_run.sh` | ✅ **46 passed** (45 pre-existing + 1 new) |

**`e2e/test_records_ai_fill.py::test_records_ai_fill_personas`** — a founder signs up, verifies, and
onboards (mirrors `e2e/test_assessment.py`'s wizard walk, then reads `active_workspace_id` off `GET
/auth/me`, same as Slice 2's `e2e/test_canvas_ai_fill.py::_onboard`) → `POST
/business-builder/personas/ai-fill` returns `202 {job_id, status: "queued"}` (captured,
`personas_ai_fill_enqueued.json`) → the worker is drained in-process (`runner.run_once` looped, with
`app.worker.handlers.ai` imported inside the drain function so the handler registers in the TEST
process, mirrors `e2e/test_canvas_ai_fill.py::_drain` / `e2e/test_notifications_email.py::_drain`)
→ `GET /business-builder/personas` now shows one stub-drafted persona record at `data.records[0]`
whose own `data.name` field reads `"[stub-llm] name"` (captured, `personas_after_fill.json`) — proving enqueue
→ job claim → structured LLM call → validated `create_record` write end to end over real HTTP with a
real Postgres-backed worker drain, zero network calls (`LLM_PROVIDER=stub`, already exported by
`scripts/e2e_run.sh` since Module 03 Slice 1 — no harness change needed this task).

**Exact JSON path to the records, confirmed live (not guessed):** `GET /{kind}` returns
`{"records": [...], "fields": [...]}` under `data` — the records are at `data.records`, an array,
**not** `data` directly (the task brief's own skeleton assertion, `got.json()["data"]`, would have
been wrong — caught by reading the real capture before writing the assertion, not assumed).
Confirmed against `e2e/_captures/records_ai_fill/personas_after_fill.json`.

**Only 1 record was drafted in this live run, not up to 3 — expected, not a bug.** The
`StubLLMClient`'s array recursion always returns a single-item array for any `{"type": "array"}`
schema node regardless of `maxItems` (see "How" above) — it's a deterministic offline stub, not a
model that samples a count. The ≤3-records path (`(result.get("records") or [])[:3]`) and the
skip-on-validation-failure path (`except AppError: continue`) are covered by
`tests/worker/test_records_ai_fill_handler.py::test_ai_fill_populates_empty_kind`, which asserts
directly against a fixture that returns a multi-record stub payload, rather than by this live e2e
journey — called out explicitly in the FE guide's verification table, not silently assumed.

## Operate / roll back

**New deploy-time requirement: none.** `business.{kind}.ai_fill` runs inside the existing `worker`
process (Module 20 Slice 2) — no new container, no new health check, no new config beyond what
Module 03 Slice 1 already introduced (`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/
`LLM_TIMEOUT`/`LLM_MAX_TOKENS`).

**Rollback:** revert this slice's commits as a unit (the range `e93c2b7..<Task 3 commit>` on
`feat/records-ai-fill`, or that branch's eventual PR merge commit on `develop`). No migration to
downgrade. The only persisted side effect is `BusinessRecord` rows created by a successful ai-fill
while this slice was live — those rows are NOT automatically reverted by rolling back the code
(there is no "undo ai-fill" operation); same judgment call Slice 2's SOP made for canvas blocks: the
AI-drafted records are real content a founder can edit or delete via the existing `PUT`/`DELETE
/{kind}/{record_id}`, not a distinct piece of state a rollback needs to undo. Reverting the code
simply means `POST /{kind}/ai-fill` goes back to enqueuing a job nothing claims meaningfully (Module
08 Slice 2's original, pre-this-slice behavior).

## Follow-ups

**No top-up or "regenerate" mode.** Ai-fill only runs when a kind has ZERO records — a kind with 1
existing record stays at 1 after calling ai-fill again, even though the founder might reasonably
expect "add a couple more personas." Building that needs a real design decision (what does
"up to 3 more" mean once some already exist — avoid duplicating the existing ones? just append?) and
is deliberately out of scope here, same reasoning Slice 2 gave for not building a canvas
"regenerate everything" mode.

**Module 03's other deferred AI consumers remain unbuilt.** This slice closes out the LAST
unconsumed `ai_fill` job type, but Module 03 stays **open**: the mission reason line, health-score
recommendation reasons, dashboard AI briefing, onboarding AI panel, and roadmap replan rationale
(all flagged across Modules 02/04/05/06's own SOPs as "deferred to Module 03") are separate,
unbuilt consumers of the same LLM seam — not mechanical copies of this slice's fill-empties pattern,
each needs its own prompt/schema/trigger design.

**No structured "ai-fill failed / still empty" signal beyond the existing job status.** Same gap
flagged in Slice 2's SOP for canvases: a failed `business.{kind}.ai_fill` job leaves `job.error` set
and the kind untouched, with no kind-level field the FE can check without also tracking the job id
(see the FE guide's §5.3 for the two polling options).

**`create_record`'s position assignment is still not race-safe** (Module 08 Slice 2's pre-existing
gap, unchanged by this slice) — two concurrent ai-fill-style bursts of `create_record` calls for the
same kind could theoretically race on `position` the same way two concurrent manual `POST /{kind}`
calls always could; this slice's single-handler-per-job execution doesn't introduce a NEW race, it
just inherits the existing one.
