# SOP — Structured LLM Output + Canvas AI Fill (Module 03, Slice 2)

**What shipped** — the second slice of Module 03 (AI Co-Founder): a **structured (JSON-shaped)
output mode** on the LLM seam (`complete_json`, alongside Slice 1's free-text `complete`), plus its
first real consumer — `business.canvas.ai_fill`, the job Module 08 Slice 1 enqueued on
`POST /canvases/{type}/ai-fill` but left with no worker since day one. That job now drafts a
canvas's empty blocks (fill-empties-only, never overwriting user-filled content) via one
`complete_json` call constrained to a strict per-canvas-type JSON Schema built from the existing
`CANVAS_BLOCKS` registry. No new API surface, no new route, no migration — same shape as Slice 1's
"reuse an existing enqueue point, add the missing handler" pattern.

Commits (branch `feat/llm-structured-output`, off `develop`):
`9de888e` (design) → `67471e2` (implementation plan, `.superpowers/sdd/
2026-09-19-llm-structured-output-canvas-fill/`) → `daa2d45` (Task 1 — `complete_json` on the seam:
Protocol method, `StubLLMClient` deterministic per-property stub, `OpenAILLMClient` strict
`json_schema` mode, `_post_chat`/`_content` DRY'd out of `complete`) → `d9d8ee4` (Task 2 —
`canvas_json_schema`, `build_canvas_fill_messages`, `handle_canvas_ai_fill`, registered as
`"business.canvas.ai_fill"`) → **this commit** (Task 3, final — live e2e + FE guide + SOP +
checklist reconcile).

## Why

Two things were blocked on this specifically, both already called out in Slice 1's own SOP
Follow-ups:

1. **The `business.canvas.ai_fill` job has existed, queued and unconsumed, since Module 08 Slice 1**
   (`docs/sop/2026-09-01-business-builder-canvas.md`) — every `POST /canvases/{type}/ai-fill` call
   in production has been enqueuing a job that no handler ever claims meaningfully (it would 404
   "no handler" and fail terminally under the runner's own dispatch — see `app/worker/runner.py`).
   Slice 1's `LLMClient.complete()` returns free text, which is the wrong shape to safely merge into
   a typed `{block_key: str | list[str]}` canvas without a fragile parse-the-prose step.
2. **Module 08's AI Business Plan Generator (§08.11)** — the one remaining unshippable Business
   Builder sub-screen — needs structured plan-section output for the same reason. This slice doesn't
   build §08.11 itself (a separate, larger consumer — a new entity, a new endpoint, a new event), but
   it unblocks it on the **capability**: `complete_json` + the strict-schema pattern this slice
   proves out end-to-end on a smaller, already-wired consumer first.

Canvas ai-fill was picked as the first structured-output consumer (over jumping straight to §08.11)
for the same reason Slice 1 picked the assessment narrative over a bigger consumer: it already had a
real enqueue point, a real typed target shape (`CANVAS_BLOCKS`) to constrain the schema against, and
a real live e2e journey (`e2e/test_business_builder.py`) to extend rather than build from scratch.

## How

**`complete_json` is a second method on the same `LLMClient` Protocol, not a new seam.**
`app/platform/llm.py`'s `LLMClient` Protocol now declares both `complete(messages, *, max_tokens,
temperature=0.7) -> str` (Slice 1, free text) and `complete_json(messages, *, schema: dict,
max_tokens: int) -> dict` (this slice, JSON-Schema-constrained). Same `get_llm_client()` factory,
same `LLM_PROVIDER` switch, same fail-loud contract — a consumer that needs structured output just
calls the other method on the client it already has.

**`StubLLMClient.complete_json`** returns a deterministic dict built directly from
`schema["properties"]`: each `type: "array"` property becomes `["[stub-llm] <key>"]`, everything
else becomes `"[stub-llm] <key>"`. No network, no key, fully offline — same `LLM_PROVIDER=stub`
convention Slice 1 established for tests/dev/e2e, extended to the new method for free.

**`OpenAILLMClient.complete_json`** sends `response_format: {"type": "json_schema", "json_schema":
{"name": "result", "schema": schema, "strict": true}}` on the same `/chat/completions` endpoint
`complete()` already calls — `strict: true` makes the model's output conform to the schema exactly
(no extra keys, no missing required keys), verified live against `gpt-5.6-luna` in Task 1's own
build-time check (a real `complete_json` call returned `{'greeting': 'hi'}` matching a toy schema,
no Responses-API migration needed — Chat Completions already supports strict `json_schema` mode).
Response body is `json.loads`'d and type-checked (`RuntimeError` on unparseable JSON or a non-dict
result) — same fail-loud posture as `complete()`'s empty-string guard.

**DRY'd `_post_chat`/`_content` out of `complete()`, not duplicated into `complete_json()`.** Task 1
extracted the shared HTTP path — build the request, POST, check status, parse the body — into two
private helpers so `complete()` and `complete_json()` differ only in the `response_format` they send
and how they interpret the response content (`.strip()`+empty-check for text vs. `json.loads`+dict
check for structured). All 8 pre-existing `complete()` tests passed unchanged after the extraction,
confirming behavior-preserving refactor, not a rewrite.

**The canvas schema is derived from `CANVAS_BLOCKS`, not hand-maintained.**
`canvas_json_schema(canvas_type)` (`app/services/business/canvas_defs.py`) walks the existing
`CANVAS_BLOCKS[canvas_type]` registry (Module 08 Slice 1) and emits one JSON Schema property per
block: `{"type": "array", "items": {"type": "string"}}` for `kind="list"` blocks, `{"type":
"string"}` for `kind="text"` blocks, every key `required`, `additionalProperties: false`. Adding a
new canvas type or block to `CANVAS_BLOCKS` automatically produces a correct schema for it — no
second registry to keep in sync, no drift possible between "what blocks this canvas has" and "what
shape the AI is constrained to produce."

**Fill-empties-only, re-read at run time — never overwrite user-filled content.**
`handle_canvas_ai_fill` (`app/worker/handlers/ai.py`) loads the canvas's *current* `blocks` at
job-run time (not whatever they were when the job was enqueued — the founder may have typed
something in the gap), computes `empty_keys` from that live read, and — after the LLM call — merges
back only the keys that were both empty AND present in the LLM's response:
`for key in empty_keys: if key in filled: merged[key] = filled[key]`. A block the founder already
filled is never touched, even if the model would have produced different content for it; a key
missing from a malformed/partial LLM response degrades gracefully to "leave that block empty"
rather than crashing. `validate_blocks` (Module 08 Slice 1's existing per-block-kind check) still
runs on the full merged dict as a final gate before persisting, `canvas.version += 1`, `db.flush()`
(not `commit()` — the runner owns the transaction, same convention as every other handler in
`app/worker/handlers/`).

**Fail-loud + job retry, no partial write.** If `complete_json` raises (bad key, non-2xx, unparseable
JSON, non-dict result — all the same fail-loud paths `complete()` already has), the handler raises
too, propagating out of the `db.begin_nested()` savepoint the runner wraps each job in
(`app/worker/runner.py::run_once`) — nothing merges, nothing persists, the canvas is left exactly as
it was, and the runner's existing retry/backoff (`WORKER_MAX_ATTEMPTS`) takes over exactly like
Slice 1's `ai.assessment.narrative`.

**Data minimization — no PII in the prompt.** `build_canvas_fill_messages`
(`app/services/business/ai_fill.py`) sends only the startup's name, industry, stage, and the block
key/label/kind lines — never founder names, emails, or any record data. Verified by
`tests/services/business/test_canvas_ai_fill.py::test_build_canvas_fill_messages_has_context_no_pii`
(asserts no `@` in the combined message content).

## What's involved

**No migration.** `BusinessCanvas.blocks` (Module 08 Slice 1's `0011_business_canvases` migration)
is reused as-is — this slice only changes what a worker writes into the existing JSONB column, never
its definition. `poetry run alembic heads` returns exactly one head
(`0025_roadmap_milestone_due_idx`), unchanged from `develop`.

**Platform seam** (`app/platform/llm.py`, Task 1)
- `LLMClient` Protocol — adds `complete_json(messages, *, schema: dict, max_tokens: int) -> dict`.
- `StubLLMClient.complete_json` / `OpenAILLMClient.complete_json` — see "How" above.
- `_post_chat` / `_content` — private helpers factored out of `complete()`, shared by both methods.

**Canvas consumer** (Tasks 2)
- `app/services/business/canvas_defs.py` — `canvas_json_schema(canvas_type: CanvasType) -> dict`.
- `app/services/business/ai_fill.py`, new — `build_canvas_fill_messages(*, name, industry, stage,
  blocks) -> list[LLMMessage]`.
- `app/worker/handlers/ai.py` — `handle_canvas_ai_fill(db, job)`, registered as
  `"business.canvas.ai_fill"` (the exact job type `POST /canvases/{type}/ai-fill` has enqueued since
  Module 08 Slice 1 — no change needed on the enqueue side, the handler just now exists).

**This task (Task 3)**
- `e2e/test_canvas_ai_fill.py`, new — see Verification below.
- `docs/fe-integration-guide-ai-canvas-fill.md`, new.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 03 gets a Slice 2 entry; Module 08's
  `business.canvas.ai_fill` "enqueue-only, no worker" deferral lines (Slices 1 and 2) updated to
  reflect the worker now shipping.
- Pre-existing formatting/lint drift fixed as part of this task's full CI reproduction (mechanical
  only, no logic change — same "final task cleans up the slice's drift" precedent as Slice 1's own
  Task 4): `black` reformatted `app/platform/llm.py`, `app/services/business/canvas_defs.py`,
  `tests/platform/test_llm.py`, `tests/services/business/test_canvas_ai_fill.py` (all left
  unformatted by Tasks 1–2, which only ran `black`/`ruff` against their own touched files, not the
  whole-repo check this task runs); `tests/services/business/test_canvas_ai_fill.py` also had a
  module-level import block placed mid-file (Task 2 appended a second batch of tests with their own
  imports below the first batch's test functions) — `ruff`'s `E402` caught it, fixed by merging both
  import blocks at the top of the file.

**Errors / API surface** — none new. `POST /canvases/{type}/ai-fill` (Module 08 Slice 1) already
returned `202 {job_id, status}` and already had its `_editor` role gate / `404` unknown-type
handling; this slice only makes the job it enqueues actually do something. The only externally
visible change is that a canvas's `blocks`/`version`/`completion` now update asynchronously after a
successful ai-fill — see the FE guide for the exact mechanics.

## Verification

**Per-task unit verification (Tasks 1–2, already green before this task):**
- `tests/platform/test_llm.py` — 14 passed (8 pre-existing `complete`/`get_llm_client` tests
  unchanged after the `_post_chat`/`_content` extraction + 6 new `complete_json` tests: stub shape,
  OpenAI happy path with schema assertion, missing key, non-2xx, unparseable JSON, non-object JSON).
- `tests/services/business/test_canvas_ai_fill.py` — 6 passed: schema strictness/shape, prompt
  content + no-PII, fills-empty-canvas (every block gets `[stub-llm]` content, `version >= 2`),
  preserves-user-filled-blocks (a pre-set block stays untouched while an empty one gets filled),
  no-op-when-startup-missing, fails-loud-when-llm-errors.

**Task 3 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests e2e` | ✅ pass (4 pre-existing files reformatted first — see "What's involved") |
| Import order | `poetry run isort --check-only app tests e2e` | ✅ pass (after fixing the mid-file import block noted above) |
| Lint | `poetry run ruff check app tests e2e` | ✅ pass (same fix cleared a 5-finding `E402` block) |
| Types | `poetry run mypy app` | ✅ pass — no issues in 157 source files |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ pass — 9.89/10 (unchanged; no new findings in AI/canvas code) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ pass, 0 findings (only informational nosec/comment-parser warnings, pre-existing) |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ pass — **1245 passed, 97.46% coverage** (≥ 95% floor) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0025_roadmap_milestone_due_idx (head)`, unchanged from `develop` |
| Live E2E | `./scripts/e2e_run.sh` | ✅ **44 passed** (43 pre-existing + 1 new) |

**`e2e/test_canvas_ai_fill.py::test_canvas_ai_fill`** — a founder signs up, verifies, and onboards
(mirrors `e2e/test_assessment.py`'s wizard walk, then reads `active_workspace_id` off `GET
/auth/me`) → `POST /business-builder/canvases/business_model/ai-fill` returns `202 {job_id,
status: "queued"}` (captured, `ai_fill_enqueued.json`) → the worker is drained in-process
(`runner.run_once` looped, mirrors `e2e/test_notifications_email.py::_drain` /
`e2e/test_ai_assessment_narrative.py`'s pattern, with `app.worker.handlers.ai` imported inside the
drain function so the handler is registered in the TEST process, not just the server process) →
`GET /business-builder/canvases/business_model` now shows every block filled with `"[stub-llm]
<key>"` content, `version: 2` (up from the lazy-created canvas's `version: 1`), and
`completion.status: "complete"` (captured, `canvas_after_fill.json`) — proving enqueue → job claim
→ structured LLM call → schema-constrained merge → persisted write end to end over real HTTP with a
real Postgres-backed worker drain, zero network calls (`LLM_PROVIDER=stub`, already exported by
`scripts/e2e_run.sh` since Slice 1 — no harness change needed this task).

**Exact JSON path to the canvas blocks, confirmed live (not guessed):** `GET
/canvases/{type}` returns them flat at `data.blocks`, a `{block_key: value}` map — same shape the
Module 08 Slice 1 FE guide already documents for a direct `GET`/`PUT`, now also the shape ai-fill
writes into. Confirmed against `e2e/_captures/canvas_ai_fill/canvas_after_fill.json`, not assumed
from the task brief's own skeleton assertion.

**Fill-empties-only (augment, not overwrite) is unit-proven, not separately re-proven live** — this
e2e journey starts from a freshly lazy-created, completely empty canvas, so every block is a valid
fill target and there's no pre-filled block in this run to prove gets skipped. That behavior is
covered instead by `tests/services/business/test_canvas_ai_fill.py::test_ai_fill_preserves_user_filled_blocks`
(sets one block before running, asserts it's untouched while an empty one gets filled) — called out
explicitly, not silently assumed, in the FE guide's verification table.

## Operate / roll back

**New deploy-time requirement: none.** `business.canvas.ai_fill` runs inside the existing `worker`
process (Module 20 Slice 2) — no new container, no new health check, no new config beyond what
Slice 1 already introduced (`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/`LLM_TIMEOUT`/
`LLM_MAX_TOKENS`, unchanged by this slice — see that SOP's "Operate" section for the production
requirements on those).

**Rollback:** revert this slice's commits as a unit (`67471e2..`this commit`` on `develop`, or the
whole branch if not yet merged). No migration to downgrade. The only persisted side effect is
`BusinessCanvas.blocks`/`version` values that were written by a successful ai-fill while this slice
was live — those rows are NOT automatically reverted by rolling back the code (there is no "undo
ai-fill" operation); this is judged acceptable for the same reason Slice 1's narrative overwrite was:
the AI-drafted blocks are real content a founder can edit or clear via the existing `PUT
/canvases/{type}`, not a distinct piece of state a rollback needs to undo. Reverting the code simply
means `POST .../ai-fill` goes back to enqueuing a job nothing claims meaningfully (Module 08 Slice
1's original, pre-Module-03 behavior).

## Follow-ups

**`business.{kind}.ai_fill` (typed records — Module 08 Slice 2) still has no worker.** This slice
only built the canvas consumer; `POST /business-builder/{kind}/ai-fill` (personas, revenue streams,
competitors, pricing) still enqueues a job with no registered handler, exactly as it has since
Module 08 Slice 2 shipped. The same `complete_json` seam this slice proved out is the natural next
step — records need a different schema shape (Pydantic model → JSON Schema, not the
`CANVAS_BLOCKS`-derived one this slice built) since `RECORD_SCHEMAS` is Pydantic v2, not the
dataclass `BlockDef` registry canvases use — a real design decision, not a trivial copy-paste, left
for a future slice.

**§08.11 AI Business Plan Generator is unblocked on the capability, still not started.** This slice
proves `complete_json` works end-to-end against a real, smaller consumer; §08.11 itself (`POST
/business-builder/plan/generate`, a new `business_plans` entity, a `business.plan.generated` event,
wiring into Module 18's `create_document(..., ai_generated=True, kind=business_plan)` seam) is a
larger, multi-section generator that still needs its own design pass — not a mechanical reuse of
this slice's single-schema-single-call pattern. Module 03 stays **open**, and Module 08 stays
**incomplete**, until §08.11 itself lands.

**No pydantic-model-driven schema variant.** `canvas_json_schema` is hand-built from `BlockDef`
tuples because that's what `CANVAS_BLOCKS` already is; a future consumer whose target shape is a
Pydantic model (records, or §08.11's plan sections) would benefit from a
`pydantic.TypeAdapter(...).json_schema()`-based helper instead of hand-writing another
`_json_schema_from_X` function per shape — not built here, deliberately, since this slice had only
one dataclass-shaped target to serve.

**No Anthropic (or other non-OpenAI-compatible) `complete_json` implementation.** Same gap Slice 1's
SOP already flagged for `complete()` — `get_llm_client()` still supports `"openai"` and `"stub"`
only; `complete_json`'s `response_format: json_schema` wire shape is itself an OpenAI-specific
convention (Anthropic's structured-output mechanism, tool-use-based, is different) that a future
Anthropic client would need to translate against, not just add a new `if provider == "anthropic"`
branch.

**No overwrite/"regenerate everything" mode.** Ai-fill is fill-empties-only by design (see "How") —
there is no way for the FE to say "redo the whole canvas, including what I already typed." If that
becomes a real product ask, it needs a new explicit opt-in (a query param or a separate endpoint),
not a change to this handler's default behavior, since fill-empties-only is the safe default a
founder would reasonably expect from a button labeled "AI fill."

**No structured "ai-fill failed / still empty" signal beyond the existing job status.** Same gap
Slice 1's SOP flagged for the assessment narrative: a failed `business.canvas.ai_fill` job leaves
`job.error` set and the canvas untouched, but there's no canvas-level field the FE can check without
also tracking the job id (see the FE guide's §3 for the two polling options and their tradeoffs).
