# Module 03 — AI Co-Founder, Slice 1: LLM seam + assessment narrative (design)

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 03 (AI Co-Founder), Slice 1 of N
**Depends on:** Module 07 (Assessment, shipped), the job worker (Module 20 Slices 2–3, shipped)

## Goal

Introduce a **provider-agnostic LLM seam** — the foundational primitive the whole
"AI Co-Founder" module and every deferred AI hook across the product will call — and
**prove it end-to-end on one real consumer**: the startup-assessment results narrative,
generated asynchronously by a worker job, replacing today's templated narrative in place.

One sentence: *build `get_llm_client()` the way `get_email_sender()` already works, and wire
one async job that has the LLM write the assessment narrative.*

## Why

- **§08.11 (AI Business Plan Generator) and ~7 other AI features are blocked** on there being
  no LLM client at all. Today `app/platform/ai.py` holds only a `StubAIPanel` Protocol with no
  consumers and no real provider call.
- The unblocking move is not any one feature — it's the **seam**: a single, tested, config-driven
  place that turns "call an LLM" into a one-liner every future slice reuses. Proving it on a small,
  already-async consumer (assessment narrative) de-risks the seam without taking on a large feature.

## Scope

### In scope (Slice 1)
1. **The LLM seam** — `app/platform/llm.py`: an `LLMClient` Protocol, an `OpenAILLMClient`
   (raw `httpx`, OpenAI / OpenAI-compatible), a `StubLLMClient` (deterministic, offline), and a
   `get_llm_client()` factory switching on `LLM_PROVIDER`, fail-loud on misconfiguration.
2. **Config** — new settings for timeout and token cap; default OpenAI model updated to
   `gpt-5.6-luna`; test/e2e environments pinned to `LLM_PROVIDER=stub`.
3. **The proof consumer** — an `ai.assessment.narrative` worker job that regenerates
   `AssessmentResult.narrative` with the LLM, enqueued at assessment completion, with the existing
   templated narrative as the immediate value and the permanent fallback.
4. **Tests** — unit (seam + factory + handler) and one live e2e journey (against the stub).
5. **Docs** — SOP, checklist reconcile, and a short FE integration guide note (the narrative field
   can change from templated → AI after completion; the FE re-fetches).

### Out of scope (explicitly deferred to later slices)
- **§08.11 AI Business Plan Generator** and canvas `ai_fill` (Module 08) — their own slices.
- **Structured / JSON-schema output** (`response_format`) — Slice 1 needs only free-text. The
  interface is shaped to grow a structured variant when the first structured consumer arrives.
- **Streaming**, conversational memory, the onboarding AI panel, dashboard briefing, mission reason
  line, roadmap re-plan rationale, health narrative — each a later consumer of the same seam.
- **A second provider (Anthropic)** — the seam is designed for it (drop-in second impl), but only
  OpenAI + stub ship in Slice 1.
- **Per-workspace token budgets / rate limiting / cost accounting** — deferred; Slice 1 caps
  `max_tokens` per call and relies on the model's low per-token price.
- **A `narrative_ai_generated` flag / migration** — no schema change in Slice 1 (see Data model).

## Architecture

### 1. The seam — `app/platform/llm.py` (new)

Mirrors `app/platform/email.py` one-for-one (Protocol → impls → factory, fail-loud).

```python
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]

@dataclass
class LLMMessage:
    role: Role
    content: str

class LLMClient(Protocol):
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int,
        temperature: float = 0.7,
    ) -> str: ...
```

- **`StubLLMClient`** — returns a deterministic, recognizable string (no network, no key). Selected
  by `LLM_PROVIDER=stub`. This is what every unit test and the e2e run against, so the suite never
  needs a key or network. The stub's output is distinct from any templated narrative so tests can
  assert "the AI path ran."
- **`OpenAILLMClient`** — POSTs the OpenAI (or OpenAI-compatible) API over the `httpx` already in the
  dependency set, exactly as `ResendEmailSender` does for Resend. Reads `LLM_API_KEY`, `LLM_MODEL`,
  `LLM_BASE_URL` (blank → OpenAI default), `LLM_TIMEOUT`. **Fail-loud**: raises `RuntimeError` on a
  missing key and on any non-2xx / timeout / malformed response (never returns a silent empty string).
  - **Request shape:** target the **Chat Completions API** (`POST {base_url}/v1/chat/completions`)
    as the provider-agnostic lingua franca (OpenAI + Azure + gateways + self-hosted all implement it).
    **Build-time verification (required):** `gpt-5.6-luna` postdates the author's model knowledge and
    the docs describe a Responses API with a `reasoning.effort` dial. Before implementing, confirm
    against **live OpenAI docs** whether `gpt-5.6-luna` is served over `/v1/chat/completions` or is
    Responses-API-only. If chat-completions works, use it (keeps gateway compatibility). If it is
    Responses-only, the `OpenAILLMClient` targets `/v1/responses` and the guide records that this
    model needs OpenAI-native access (no generic gateway). Either way the `LLMClient` interface is
    unchanged — the endpoint choice is an implementation detail behind `complete()`.
- **`get_llm_client() -> LLMClient`** — switches on `settings.LLM_PROVIDER`:
  - `stub` → `StubLLMClient()` (no key required)
  - `openai` → `OpenAILLMClient()` (raises if `LLM_API_KEY` is empty)
  - unknown value → `RuntimeError` naming the offending value and the allowed set.
  Not memoized in Slice 1 (cheap to construct; a new client per call is fine — same as
  `get_email_sender()`).

### 2. Config — `app/core/config.py`

| Setting | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` \| `stub` (Anthropic later). |
| `LLM_API_KEY` | `""` | Required for `openai`; unused by `stub`. |
| `LLM_MODEL` | `gpt-5.6-luna` | **Changed from `gpt-4o-mini`.** OpenAI example default. |
| `LLM_BASE_URL` | `""` | Blank → OpenAI default; set for Azure/gateway/self-hosted. |
| `LLM_TIMEOUT` | `60` | Seconds per LLM HTTP call (LLM latency ≫ a normal request). |
| `LLM_MAX_TOKENS` | `800` | Default output cap; the narrative call passes this. |

- `.env.example` and `.env.production.example`: update the OpenAI example line to
  `LLM_MODEL=gpt-5.6-luna` and add `LLM_TIMEOUT` / `LLM_MAX_TOKENS`.
- **Test + e2e environments set `LLM_PROVIDER=stub`** so the suite is offline and deterministic and
  never requires `LLM_API_KEY`. (Exact mechanism — test env file / conftest env — settled in the plan.)

### 3. The proof consumer — assessment narrative

**Trigger (enqueue at completion).** In `app/services/assessment/service.py`, right where
`roadmap.replan` is already enqueued on completion (≈ line 218), add:

```python
job_dispatcher.enqueue(db, "ai.assessment.narrative", job_payload, startup.id)
```

`job_payload` already is `{"startup_id": ..., "assessment_id": ...}`. The templated narrative is
still written to `AssessmentResult.narrative` in the same transaction, so the FE gets a narrative
immediately; the job upgrades it later. (Direct enqueue, co-located with `roadmap.replan`, is chosen
over an `assessment.completed` event-subscriber for simplicity and to match the neighbouring code.)

**Prompt builder (domain logic, in the consumer — not the seam).** A pure function, e.g.
`app/services/assessment/narrative.py::build_narrative_messages(*, dimension_scores, overall, industry, stage) -> list[LLMMessage]`,
turns the scored assessment + startup profile into a system+user message pair. **Data minimization:**
it sends only business context (industry, stage, per-dimension scores, overall score) — **no names,
emails, or other PII**.

**Handler — `app/worker/handlers/ai.py` (new).**
```python
def handle_assessment_narrative(db: Session, job: Job) -> None:
    # 1. load the AssessmentResult (+ assessment, startup profile) by assessment_id from job.payload
    # 2. messages = build_narrative_messages(...)
    # 3. text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)
    # 4. result.narrative = text.strip()
    # (no db.commit / rollback — the runner owns the transaction, same as the other handlers)
register_handler("ai.assessment.narrative", handle_assessment_narrative)
```
`app/worker/__main__.py::register()` also imports `app.worker.handlers.ai` for its registration
side-effect (same pattern as `handlers.email` / `handlers.scheduled`).

### Data model

**No migration.** `AssessmentResult.narrative` (`Text`, `app/db/models/assessment.py:90`) already
exists and holds the narrative; the job overwrites it. No new columns, no new table. (A
`narrative_ai_generated` boolean would aid observability but is a migration + scope; deferred.)

## Data flow

```
POST /assessments/{id}/complete
  └─ score() → write AssessmentResult(narrative = TEMPLATED)   ← instant value + fallback
  └─ recompute_health_score(...)
  └─ enqueue "roadmap.replan"
  └─ enqueue "ai.assessment.narrative"        ← NEW
  └─ publish "assessment.completed"
  └─ (commit)
         … worker loop …
  run_once → claim "ai.assessment.narrative" job
     └─ load result + context → build_narrative_messages()
     └─ get_llm_client().complete(messages, max_tokens=…)      ← the only network call
     └─ result.narrative = AI text        ← overwrite in place
     └─ (runner commits)
FE re-fetches GET /assessments/{id} → narrative is now the AI version
  (or still TEMPLATED if the job is mid-flight or permanently failed)
```

## Error handling

- **Client is fail-loud.** Missing key, non-2xx, timeout, or unparseable body → `RuntimeError`. It
  never returns an empty or partial narrative silently.
- **The job runner is the retry policy.** A raised error marks the job for the existing bounded
  retry + backoff (`jobs.attempts` / `run_after`). Transient LLM failures (429/5xx/timeout) recover
  on a later attempt with no special code.
- **The templated narrative is the permanent fallback.** If the job exhausts `WORKER_MAX_ATTEMPTS`
  and lands terminal-failed, `result.narrative` simply remains the templated text — the user always
  has a coherent narrative. (Same one-shot exposure class the scheduler SOP documents; acceptable.)
- **Re-run safety.** Running the job twice just regenerates the narrative (overwrite) — no
  duplication, no corruption.

## Testing

- **Unit — seam:** `StubLLMClient.complete` returns its deterministic string; `get_llm_client()`
  returns the right impl per `LLM_PROVIDER` and raises on unknown value / missing key;
  `OpenAILLMClient` with a **mocked `httpx`** proves the request is well-formed and the fail-loud
  paths (non-2xx, timeout, malformed body) raise.
- **Unit — prompt builder:** `build_narrative_messages` includes the scores/industry/stage and
  **excludes PII**.
- **Unit — handler:** enqueue → `run_once` with `LLM_PROVIDER=stub` → `result.narrative` equals the
  stub output (proves load → call → persist), plus a fail-loud path (LLM raises → job not marked done).
- **E2E (stub):** complete a real assessment via the API → drain the worker in-process → re-fetch
  the assessment → `narrative` equals the stub's AI output (not the templated text). Capture the
  before/after result bodies. Deterministic because the e2e env uses `LLM_PROVIDER=stub`.
- Coverage stays ≥ 95%.

## Security & privacy

- **`LLM_API_KEY` is a secret** — read from env only, **never logged** (the client must not log the
  key or full request headers). Encrypted env on staging/prod, as with `RESEND_API_KEY`.
- **Data leaves to a third party.** The narrative call sends startup business context (industry,
  stage, dimension scores) to OpenAI. This is inherent to an AI co-founder and expected, but is
  called out so it is a conscious product decision. **Data minimization is enforced in the prompt
  builder** — no names, emails, or identifiers, only the business signals the narrative needs.
- No new endpoints, no change to RBAC/tenancy, no new external inbound surface.

## FE impact

- **The `narrative` field on the assessment result can change after completion** — templated at first,
  then the AI version once the job runs (usually seconds), and it stays templated if the LLM job
  fails. The FE should treat the assessment result as re-fetchable and not cache the narrative as
  final at completion time. Documented in a short FE integration guide note; no shape change (still a
  string on the existing result payload).

## Global constraints (carried into the plan)

- **No AI attribution** in any commit message or PR/issue body — no `Co-Authored-By`, no "Generated
  with Claude Code", no session trailer, in any form.
- **Reproduce every CI check locally and make it green before pushing** (black/isort/ruff/mypy/pylint
  ≥ 9.5/bandit/pytest ≥ 95% cov/alembic single head/e2e), using the project's pinned toolchain via
  `poetry run`.
- **Ship the SOP** (`docs/sop/`), reconcile the **checklist**
  (`docs/checklist/PROJECT_CHECKLIST.md`), and write the **FE integration guide** note with payloads
  copied verbatim from live e2e captures.
- Response envelope, `AppError`, RBAC helpers, enum-column and FK-index conventions unchanged.
- **No migration in this slice** (assert one alembic head, unchanged from `develop`).

## Follow-ups (post-Slice-1)

- Structured / JSON-schema output on the seam → unblocks §08.11 and canvas `ai_fill`.
- Second provider (Anthropic) impl behind the same factory.
- The remaining deferred AI consumers (onboarding panel, dashboard briefing, mission reason line,
  roadmap re-plan rationale, health narrative) — each its own slice on this seam.
- `narrative_ai_generated` flag (+ migration) for observability, if it becomes useful.
- Per-workspace token budgets / cost accounting / rate limiting.
- Retire the now-superseded `StubAIPanel` in `app/platform/ai.py` when the onboarding-panel slice
  lands on the real seam.
```
