# Module 03 — Records `ai_fill` worker (design)

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 03 (AI Co-Founder) consumer / Module 08 records — completing the last unconsumed `ai_fill` jobs.
**Depends on:** Module 03 Slice 1 (LLM seam) + Slice 2 (structured output `complete_json`) — shipped; Module 08 records (`create_record`, `RECORD_SCHEMAS`) — shipped; the job worker.

## Goal

Make the already-enqueuing `business.{kind}.ai_fill` jobs (personas / revenue streams / competitors /
pricing) actually run: when a record kind is empty, the worker generates up to 3 AI-drafted records
of that kind via structured output and appends them.

One sentence: *the canvas ai_fill pattern (Slice 2), applied to the four typed record kinds, emitting
an array of validated records.*

## Why

- `POST /business-builder/{kind}/ai-fill` (shipped) enqueues `business.{kind}.ai_fill` but **no worker
  consumes it** — those jobs sit `queued` forever. This is the last unconsumed `ai_fill` job type
  after the canvas worker shipped (Slice 2).
- It reuses the structured-output capability directly, on a schema the codebase already defines
  (`RECORD_SCHEMAS`), so it's a low-risk, high-signal slice.

## Scope

### In scope
1. **Seam enhancement** — `StubLLMClient.complete_json` recurses nested schemas (object properties,
   array items, enums, numbers, booleans) so it yields *valid nested objects* for any schema, not just
   flat `{key: string|array-of-string}`. (Additive; existing flat callers unchanged.)
2. **`record_json_schema(kind)`** — an OpenAI-strict JSON schema per record kind.
3. **`handle_record_ai_fill` worker** — registered for all four `business.{kind}.ai_fill` job types;
   fill-empties-only, up to 3 records, validate-and-append via `create_record`.
4. Tests (unit + one live e2e against the stub), FE guide note, SOP, checklist.

### Out of scope (deferred)
- Top-up / append-to-non-empty and replace-all modes (v1 is fill-empties-only).
- A new notification (record creation already fires `business.artifact.completed` on the first record
  of a kind via `create_record` — reused, nothing new).
- The other Module 03 consumers (mission reason line, health recommendations, dashboard briefing,
  onboarding panel, roadmap re-plan rationale) — their own later slices.
- Anthropic provider impl.

## Architecture

### 1. Seam — `StubLLMClient.complete_json` recursion (`app/platform/llm.py`)

Replace the flat stub builder with a recursive one that honors any JSON-schema node:
- `object` → a dict with every property built recursively (respecting `required`/`properties`).
- `array` → a single-element list of the `items` schema built recursively.
- `string` → `"[stub-llm] <key>"` (key = the property name in context, else `"value"`).
- `integer`/`number` → `0`.
- `boolean` → `false`.
- `enum` present → the first enum value.

So a records schema (`{records: [<record object>]}`) yields one *valid* record object whose fields
pass the kind's pydantic validation. `OpenAILLMClient.complete_json` is unchanged. This makes the
structured stub correct for nested schemas generally, not just this slice.

### 2. `record_json_schema(kind)` — `app/services/business/record_defs.py`

A strict, OpenAI-compatible schema per kind: a root object with a single `records` array
(`maxItems: 3`) whose `items` is the kind's object schema — every field `required`,
`additionalProperties: false`, primitive types only (`string`/`number`/`boolean`/`array`), enums as
`enum` lists (e.g. `ThreatLevel`, `PricingModelType`), nested objects (e.g. pricing `tiers` →
`PricingTier`) expanded inline. Unsupported JSON-schema keywords (`minimum`/`maximum` from
`map_x/map_y` `ge/le`) are omitted — the pydantic model (`RECORD_SCHEMAS[kind]`) + `create_record`
remain the real validator, so the schema only needs to be strict-valid and shape-correct. The four
schemas are authored explicitly (only four, small) to avoid pydantic `$defs`/strictification wrangling.

### 3. Worker — `app/worker/handlers/ai.py` (extend) or a focused module

`handle_record_ai_fill(db, job)`:
1. `kind = RecordKind(job.payload["kind"])`; `startup = db.get(Startup, job.payload["startup_id"])`;
   no-op if startup missing.
2. **Fill-empties:** if any `BusinessRecord` of that kind exists for the startup → return (no-op).
3. `result = get_llm_client().complete_json(build_record_fill_messages(kind, startup), schema=record_json_schema(kind), max_tokens=settings.LLM_MAX_TOKENS)`.
4. For each record in `result.get("records", [])[:3]`: call `create_record(db, startup, kind, rec)`
   inside a `try/except AppError` — **skip** a record that fails pydantic validation (log it), create
   the valid ones. (A partly-valid response still yields useful records; a fully-invalid one creates
   nothing — a benign no-op.)
5. No `db.commit`/`rollback` (runner owns the txn); `create_record` flushes each row. Fail-loud on the
   LLM call itself (the seam raises → job retries).

Register the handler under all four types:
`for k in RecordKind: register_handler(f"business.{k.value}.ai_fill", handle_record_ai_fill)`.
(`app/worker/__main__.py::register()` already imports `handlers.ai`.)

**Prompt builder** `build_record_fill_messages(kind, startup)` (in `app/services/business/ai_fill.py`,
alongside the canvas one): PII-free (startup name/industry/stage + the kind's field labels); asks for
up to 3 realistic records of that kind.

### Data model

**No migration.** `business_records` already exists; the worker appends rows via `create_record`.

## Data flow

```
POST /business-builder/{kind}/ai-fill  (202, already shipped) → enqueue business.{kind}.ai_fill {startup_id, kind}
     … worker …
  handle_record_ai_fill → (kind empty?) → complete_json(record_json_schema(kind)) → up to 3 records
     → create_record per valid record (pydantic-validated; first one fires business.artifact.completed)
GET /business-builder/{kind} → the new records
```

## Error handling

- **Fill-empties**: only acts when the kind has zero records → never duplicates/clobbers user records;
  re-run after records exist is a no-op.
- **Per-record validation**: an individual record failing the pydantic schema is skipped (logged), not
  fatal — the rest still land.
- **LLM failure**: fail-loud → job retry/backoff; nothing partial persists beyond the records already
  created in that attempt (on a mid-loop non-validation crash the savepoint rolls back the batch).
- PII-free prompt; `LLM_API_KEY` never logged.

## Testing

- **Unit — stub recursion:** `StubLLMClient.complete_json` on a nested schema (`{records: array of
  object-with-string+number+enum}`) returns a dict whose `records[0]` is a valid object (string
  fields `[stub-llm] …`, number `0`, enum = first value).
- **Unit — `record_json_schema`:** for each `RecordKind`, root has `records` array (`maxItems 3`),
  items object all-required + `additionalProperties:false`, enums present for the enum fields.
- **Unit — handler:** with `LLM_PROVIDER=stub`, an empty kind → up to 3 records created (pydantic-valid,
  correct `kind`); a kind that already has a record → no-op (count unchanged); LLM error → fail-loud.
- **E2E (stub):** onboard founder → `POST /business-builder/personas/ai-fill` (202) → drain worker →
  `GET /business-builder/personas` shows the AI-drafted records (stub markers). Capture 202 + list.
- Coverage ≥ 95%.

## Security & privacy

Trigger already `_editor`-gated. Prompt carries only business context (no PII). `LLM_API_KEY` env-only,
never logged. No new endpoints.

## FE impact (integration guide)

Extend the canvas ai-fill guide (or a short records section): `POST /business-builder/{kind}/ai-fill`
(202, `{job_id, status}`) now completes async — an **empty** record kind gets up to 3 AI-drafted
records within seconds; if the kind already has records, it's a no-op (ai-fill seeds an empty list, it
doesn't top up or overwrite in v1). Poll `GET /jobs/{id}` or re-fetch `GET /business-builder/{kind}`.
Payloads verbatim from captures.

## Global constraints (carried into the plan)

- **No AI attribution**; reproduce CI locally & green before push (black/isort/ruff/mypy/pylint ≥
  9.5/bandit/pytest ≥ 95%/**single alembic head unchanged — no migration**/e2e); DB-clean unit tests
  (`db` fixture, no `SessionLocal()` on the app DB); SOP + checklist + FE guide from live captures.
- Worker no-commit convention; seam fail-loud; enum/FK conventions unchanged.

## Follow-ups

Top-up/replace ai-fill modes; the remaining Module 03 consumers (mission reason line, health
recommendations, dashboard briefing, onboarding panel, roadmap re-plan rationale); Anthropic provider.
