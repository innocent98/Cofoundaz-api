# Module 03 — AI Co-Founder, Slice 2: structured LLM output + canvas ai_fill (design)

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 03 (AI Co-Founder), Slice 2 of N
**Depends on:** Module 03 Slice 1 (the LLM seam — shipped, PR #72), Module 08 Business Builder (canvas — shipped), the job worker.

## Goal

Add **structured / JSON-schema output** to the LLM seam — the capability §08.11 and the `ai_fill`
workers need — and **prove it** by wiring the already-enqueuing **`business.canvas.ai_fill`** worker:
the LLM drafts a Business/Lean canvas as a schema-constrained object, and the worker fills the empty
blocks.

One sentence: *add `complete_json(messages, schema) -> dict` to the seam, and let the canvas ai_fill
job use it to draft empty canvas blocks.*

## Why

- Slice 1 shipped free-text generation only. §08.11 (AI Business Plan Generator) and the
  `business.*.ai_fill` jobs need the model to return a **specific object shape**, not prose.
- The `business.canvas.ai_fill` job already exists and users can already trigger it
  (`POST /business-builder/canvases/{type}/ai-fill`, 202), but **no worker consumes it** — the jobs
  sit `queued` forever. This slice makes them run, and does so on a schema the codebase already
  defines (`CANVAS_BLOCKS`), so it's a low-risk, high-signal proof of structured output.

## Scope

### In scope (Slice 2)
1. **Structured output on the seam** — `LLMClient.complete_json(messages, *, schema, max_tokens) -> dict`
   (`OpenAILLMClient` via `response_format` json_schema strict; `StubLLMClient` deterministic from the
   schema; fail-loud).
2. **Canvas JSON-schema builder** — derive a strict JSON Schema from `CANVAS_BLOCKS[type]`.
3. **The proof consumer** — `business.canvas.ai_fill` worker: draft empty blocks, persist, bump version.
4. **Tests** (unit + one live e2e against the stub), **FE guide**, **SOP**, **checklist**.

### Out of scope (deferred)
- **`business.{kind}.ai_fill`** (records: personas/revenue-streams/competitors/pricing) worker — a
  follow-on using the same capability (records have per-kind shapes in `record_defs.py`).
- **§08.11 AI Business Plan Generator** — its own Module 08 slice on this capability.
- **A pydantic-model seam variant** (`complete_model`) — add if a consumer wants typed output; the
  dynamic canvas schema is dict-shaped, so dict-in/dict-out is the right primitive now.
- **Overwriting user-filled blocks / prior-version snapshots** — this slice fills only empty blocks.
- **Anthropic structured-output impl** — OpenAI + stub only.

## Architecture

### 1. Seam — `app/platform/llm.py`

Add to the `LLMClient` Protocol and both impls:

```python
def complete_json(
    self, messages: list[LLMMessage], *, schema: dict, max_tokens: int
) -> dict: ...
```

- **`OpenAILLMClient.complete_json`** — same request path as `complete`, plus
  `"response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}`.
  Parse `choices[0].message.content` as JSON; **fail-loud** on missing key / non-2xx / transport error
  / unparseable JSON / non-object result. (Reuses the `LLM_BASE_URL` full-base convention and
  `max_completion_tokens` from Slice 1; `temperature` still not forwarded.)
  - **Build-time verification (required):** confirm against live OpenAI docs + one real smoke call
    that `gpt-5.6-luna` supports `response_format: json_schema` (strict) over `/v1/chat/completions`
    (Slice 1's smoke-call discipline — the model postdates the author's knowledge). If it needs the
    Responses API for structured output, adapt the impl behind `complete_json` (interface unchanged).
    Never commit a test that depends on the real key/network — the stub covers tests.
- **`StubLLMClient.complete_json`** — deterministic, offline: walk `schema["properties"]` and fill
  each by declared type — `"string"` → `"[stub-llm] <key>"`, `"array"` → `["[stub-llm] <key>"]`.
  So a filled canvas carries recognizable `[stub-llm]` markers a test/e2e asserts.
- The seam stays generic: it returns the parsed dict; **domain validation lives in the consumer**
  (no `jsonschema` dependency added).

### 2. Canvas schema builder — `app/services/business/canvas_defs.py`

```python
def canvas_json_schema(canvas_type: CanvasType) -> dict:
    props = {}
    for b in CANVAS_BLOCKS[canvas_type]:
        props[b.key] = {"type": "string"} if b.kind == "text" else {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": props,
        "required": list(props),           # strict mode: every property required
        "additionalProperties": False,     # strict mode: no extra keys
    }
```

### 3. Consumer — `business.canvas.ai_fill` worker (`app/worker/handlers/ai.py`)

`handle_canvas_ai_fill(db, job)`:
1. Read `startup_id`, `canvas_type` from `job.payload`; load `Startup`; parse `CanvasType`.
2. `canvas = get_or_create_canvas(db, startup, canvas_type)` — re-read at run time (respects edits
   made between trigger and run).
3. Determine currently-empty blocks (existing `_is_filled` / `completion` logic in
   `app/services/business/service.py`).
4. `schema = canvas_json_schema(canvas_type)`; build a **PII-free** prompt
   (`build_canvas_fill_messages`: startup **name/industry/stage** + the block labels; ask to draft
   each block; lists as arrays of short strings). No user names/emails.
5. `filled = get_llm_client().complete_json(messages, schema=schema, max_tokens=settings.LLM_MAX_TOKENS)`.
6. **Merge fill-empties-only:** for each block key, if the current block is empty, take
   `filled[key]`; otherwise keep the user's value.
7. `validate_blocks(canvas_type, merged)` (existing) → set `canvas.blocks = merged`, `canvas.version += 1`.
   No `db.commit`/`rollback` (the runner owns the transaction).

`register_handler("business.canvas.ai_fill", handle_canvas_ai_fill)` at import (mirrors Slice 1's
`ai.assessment.narrative`; `app/worker/__main__.py::register()` already imports `handlers.ai`).

### Data model

**No migration.** `BusinessCanvas.blocks` (JSONB) + `version` already exist; the worker updates them.

## Data flow

```
POST /business-builder/canvases/{type}/ai-fill  (202, already shipped)
   └─ enqueue business.canvas.ai_fill {startup_id, canvas_type}; commit
        … worker …
  run_once → handle_canvas_ai_fill
     └─ get_or_create_canvas → empties = blocks currently empty
     └─ schema = canvas_json_schema(type); messages = build_canvas_fill_messages(...)
     └─ filled = get_llm_client().complete_json(messages, schema=schema)   ← the network call
     └─ merge (fill empties only) → validate_blocks → canvas.blocks = merged; version += 1
     └─ (runner commits)
GET /business-builder/canvases/{type} → filled blocks, higher completion%
```

## Error handling

- **Fail-loud client** (missing key / non-2xx / unparseable / non-object) → the job's existing
  retry/backoff acts; on permanent failure the canvas is simply left as-is (empty blocks stay empty —
  no corruption, no partial write, because the merge+validate+assign happens only after a successful
  parse).
- **`validate_blocks` guard**: if the (strict-schema) result somehow fails domain validation, the
  handler raises (job retries) rather than persisting a malformed canvas.
- **Re-run safety**: running twice just re-fills whatever is still empty; already-filled blocks are
  preserved, so it's idempotent-ish and never destructive.
- **Concurrency**: re-reading the canvas at run time + fill-empties-only means a user edit landing
  between trigger and run is preserved; `version += 1` reflects the change.

## Testing

- **Unit — seam:** `StubLLMClient.complete_json` returns a dict matching the schema's keys/types with
  `[stub-llm]` markers; `OpenAILLMClient.complete_json` with **mocked httpx** proves the request
  carries `response_format.json_schema.strict=true` + the schema, parses the object, and fail-louds
  on non-2xx / unparseable / non-object.
- **Unit — schema builder:** `canvas_json_schema` maps text→string, list→array-of-strings, sets
  `required` = all keys and `additionalProperties=false`, for each `CanvasType`.
- **Unit — handler:** with `LLM_PROVIDER=stub`, an empty canvas → all blocks filled with `[stub-llm]`
  values, `validate_blocks` passes, `version` bumped; a **partially-filled** canvas → user blocks
  preserved, only empties filled; missing startup / already-complete canvas handled; LLM error →
  handler raises (job not marked done).
- **E2E (stub):** onboard founder → `POST /business-builder/canvases/business_model/ai-fill` (202) →
  drain worker in-process (import `app.worker.handlers.ai`) → `GET` the canvas → blocks filled
  (`[stub-llm]` markers), completion% up. Capture the 202 + the filled canvas. Set `LLM_PROVIDER=stub`
  (already in `scripts/e2e_run.sh` from Slice 1).
- Coverage ≥ 95%.

## Security & privacy

- `LLM_API_KEY` from env only, never logged (unchanged). The prompt carries only **business context**
  (startup name, industry, stage, block labels) — no user names/emails/PII. Data leaves to OpenAI as
  in Slice 1 (documented, expected for an AI co-founder). No new endpoints; RBAC/tenancy unchanged
  (the trigger endpoint already enforces `_editor`).

## FE impact (integration guide)

- `POST /business-builder/canvases/{type}/ai-fill` now actually completes asynchronously: the canvas's
  empty blocks get AI drafts within seconds. The FE should poll the job (`GET /jobs/{id}`, existing)
  or re-fetch the canvas, and treat **ai-fill as augmentation** — it fills empty blocks and never
  overwrites content the user already entered. `version` increments when it lands.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit or PR/issue body.
- **Reproduce every CI check locally and make it green before pushing** (black/isort/ruff/mypy/pylint
  ≥ 9.5/bandit/pytest ≥ 95% cov/alembic single head **unchanged — no migration**/e2e), via `poetry run`.
  Unit tests must pass against a **clean DATABASE_URL** (use the `db` fixture / DB-independent tests —
  do not call `SessionLocal()` against the app DB, per the Slice-4 lesson).
- **Ship the SOP**, reconcile the **checklist**, write the **FE integration guide** with payloads
  copied verbatim from live e2e captures.
- Response envelope, `AppError`, worker no-commit convention, seam fail-loud style unchanged.

## Follow-ups (post-Slice-2)

- `business.{kind}.ai_fill` (records) worker on the same capability (per-kind schemas from `record_defs.py`).
- §08.11 AI Business Plan Generator (Module 08) on `complete_json`.
- Optional `complete_model` (pydantic) seam variant for typed consumers.
- Anthropic structured-output impl.
- An "overwrite / regenerate (with restore)" ai-fill mode, if product wants it beyond fill-empties.
```
