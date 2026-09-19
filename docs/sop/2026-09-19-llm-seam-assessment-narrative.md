# SOP — LLM Seam + Assessment Narrative (Module 03, Slice 1)

**What shipped** — the first slice of Module 03 (AI Co-Founder): a **provider-agnostic LLM seam**
(`app/platform/llm.py`) with a fail-loud `openai` client and a deterministic `stub` client for
tests/e2e, plus **one real consumer** wired onto an existing flow — on assessment completion, a new
`ai.assessment.narrative` background job calls the LLM and overwrites `AssessmentResult.narrative`
(previously templated-only, Module 07) with an AI-generated narrative. No new API surface, no new
route, no migration — the job reuses the existing `roadmap.replan`-style enqueue path and the
existing `narrative` column.

Commits (branch `feat/llm-seam-assessment-narrative`, off `develop`):
`4cadb05` (design) → `f38f2ec` (implementation plan, `.superpowers/sdd/
2026-09-19-llm-seam-assessment-narrative/`) → `90c738c` (Task 1 — `LLM_*` config settings,
`gpt-5.6-luna` default) → `5aea9bb` (Task 1 fix — env-independent default-assertion test) →
`ae3c90e` (Task 2 — the seam: `LLMMessage`/`LLMClient` Protocol/`StubLLMClient`/`OpenAILLMClient`/
`get_llm_client()`) → `aad37f2` (Task 2 fix — `LLM_BASE_URL` convention: full base incl. `/v1`, no
double `/v1`) → `92a9ae2` (Task 3 — prompt builder + `handle_assessment_narrative` + enqueue-at-
completion + registration) → **this commit** (Task 4, final — live e2e + captures + FE guide + SOP
+ checklist).

## Why

Nine already-shipped modules (Business Builder §08.11's AI Business Plan Generator, Dashboard's AI
briefing, Mission's AI-authored reason line, Roadmap's AI-authored replan rationale, Health Score's
recommendation reasons, Learning's recommendations, Validation Hub's insight synthesizer, plus
Module 07's own narrative and the onboarding AI panel) each carry a "deferred to Module 03" line in
their own SOP or checklist entry — none of them can move until an actual LLM integration exists
anywhere in the codebase. Rather than build all of those consumers before proving the seam works
end to end, this slice builds the seam ONE TIME and wires exactly ONE consumer (the assessment
narrative — already had a templated placeholder to upgrade, and a real live e2e journey
(`e2e/test_assessment.py`) already existed to drive it) all the way through a real HTTP → worker →
LLM → persisted-field round trip. Every other deferred AI consumer above can now follow the same
seam without re-deciding provider abstraction, config, or error handling.

## How

**The seam mirrors the existing email seam's shape on purpose** (`app/platform/email.py`'s
`EmailSender` Protocol + `FileEmailSender`/`ResendEmailSender` + `get_email_sender()`) —
`app/platform/llm.py`'s `LLMClient` Protocol + `StubLLMClient`/`OpenAILLMClient` +
`get_llm_client()`, switched on `settings.LLM_PROVIDER` the same way email switches on
`EMAIL_BACKEND`. Reusing an established pattern in this codebase rather than inventing a new one
means every consumer author already knows the shape: call `get_llm_client().complete(...)`, don't
hold a client reference across requests, and the stub variant is what tests/e2e use by default.

**Async job, not an inline LLM call on the completion request.** `complete_assessment`
(`app/services/assessment/service.py:219`) enqueues `ai.assessment.narrative` alongside the
pre-existing `roadmap.replan` enqueue, using the SAME `job_dispatcher.enqueue` call and the SAME
`jobs` table Module 20's worker already drains — no new infrastructure. An LLM call is slow
(seconds, `LLM_TIMEOUT` defaults to 60s) and occasionally unavailable; making the founder's
completion request wait on it (or fail because of it) would turn a reliable, fast, already-scored
completion into a flaky one. The templated narrative (Module 07, computed synchronously with no
external dependency) is returned immediately; the AI version lands whenever the worker next drains
the queue.

**Templated-first, AI-upgrade-second, templated-forever-on-failure.** `score()`
(`app/services/assessment/scoring.py`) still computes and stores a templated narrative at
completion time exactly as before this slice — that write is completely unchanged. The new job
(`app/worker/handlers/ai.py::handle_assessment_narrative`) is purely additive: it re-fetches the
`AssessmentResult`, calls the LLM, and OVERWRITES `narrative` in place. If the LLM call raises (see
fail-loud below), the handler raises too, the runner's existing retry/backoff
(`WORKER_MAX_ATTEMPTS=5` default) retries the job a few times, and if it still fails, the row is
simply left at its templated value forever — there is no separate "AI failed" flag and no silent
fallback write. This was a deliberate choice over, say, retrying indefinitely or surfacing a
user-visible error: a templated narrative is a genuinely good, honest fallback, not a broken state
worth alarming the founder about.

**Fail-loud client, by design, so the job's retry can act on real signal.**
`OpenAILLMClient.complete()` never returns a silent empty/partial string — a missing
`LLM_API_KEY`, non-2xx response, transport error, unparseable body, or empty completion text all
raise `RuntimeError`. This mirrors the existing `ResendEmailSender`'s fail-loud contract
(`docs/sop/2026-09-12-resend-email-backend.md`) — a silently-swallowed LLM failure would look
identical to "the AI just didn't have anything to say," which is a much worse debugging experience
than a job landing in the worker's terminal-failed state with a real exception attached.

**Live-verified request shape for `gpt-5.6-luna` — two deviations from a naive OpenAI Chat
Completions implementation, confirmed against the real API while building Task 2 (not guessed):**
- Sends `max_completion_tokens`, not `max_tokens` — OpenAI deprecated `max_tokens` for this model
  family (`app/platform/llm.py:43-47`).
- Does **not** forward `temperature` — `gpt-5.6-luna` 400s on any explicit value (including this
  seam's own default of `0.7`) with `"Only the default (1) value is supported"`, the same
  restriction OpenAI applies to o-series reasoning models. `temperature` stays in `complete()`'s
  signature for Protocol conformance and for any future provider/model that does honor it —
  `OpenAILLMClient` just has nowhere live to put it for the model this deployment is configured
  with today.

**`LLM_BASE_URL` is the full API base INCLUDING `/v1`** (OpenAI SDK convention), not just the host —
fixed in `aad37f2` after Task 2's review caught that the original implementation would have produced
a double `/v1` (`.../v1/v1/chat/completions`) against the real deployment's env value. Blank
(`""`, the default) resolves to `https://api.openai.com/v1`; an Azure/gateway/self-hosted
OpenAI-compatible endpoint sets this to its own base including whatever path segment plays the same
role as `/v1`.

**Data minimization — no PII reaches the LLM.** `build_narrative_messages`
(`app/services/assessment/narrative.py`) sends only dimension scores, the overall score, industry,
and stage — never the founder's name, email, or raw answer text. Verified by
`tests/services/assessment/test_narrative.py` and re-confirmed in this task's FE guide (§4).

**Stub mode for tests/dev/e2e — this task's own contribution.** `LLM_PROVIDER=stub` (this task adds
the export to `scripts/e2e_run.sh`, alongside the pre-existing `EMAIL_BACKEND=file`) makes
`get_llm_client()` return `StubLLMClient`, whose `complete()` returns a fixed
`"[stub-llm] AI-generated assessment narrative."` string with no network call and no API key —
deterministic and assertable. This pins BOTH the e2e server process (spawned by the script, inherits
the exported env) and the pytest process (which drains the worker in-process, and therefore also
calls `get_llm_client()` directly) to the same offline client.

## What's involved

**No migration.** `AssessmentResult.narrative` (Module 07's `0004_assessment` migration) is reused
as-is — this slice only changes *when* and *how many times* it's written, never its column
definition. `poetry run alembic heads` returns exactly one head
(`0025_roadmap_milestone_due_idx`), unchanged from `develop`.

**Platform seam** (`app/platform/llm.py`, new, Task 2)
- `LLMMessage` (`role: Literal["system","user","assistant"]`, `content: str`) — the wire-agnostic
  message shape every consumer builds against.
- `LLMClient` Protocol — `complete(messages, *, max_tokens, temperature=0.7) -> str`.
- `StubLLMClient` — see "Stub mode" above.
- `OpenAILLMClient` — see "Fail-loud client" / "Live-verified request shape" above.
- `get_llm_client()` — factory switching on `settings.LLM_PROVIDER` (`"openai"` | `"stub"`);
  unknown value raises `RuntimeError` at call time (not at import time — no settings validator).

**Config** (`app/core/config.py`, Task 1) — `LLM_PROVIDER: str = "openai"`,
`LLM_API_KEY: str = ""`, `LLM_MODEL: str = "gpt-5.6-luna"`, `LLM_BASE_URL: str = ""`,
`LLM_TIMEOUT: int = 60`, `LLM_MAX_TOKENS: int = 800`. `.env.example` documents the
provider-agnostic scheme and the `/v1`-inclusive `LLM_BASE_URL` convention (lines 89-104).

**Consumer** (Task 3)
- `app/services/assessment/narrative.py`, new — `build_narrative_messages(*, dimension_scores,
  overall, industry, stage) -> list[LLMMessage]`. See "Data minimization" above.
- `app/worker/handlers/ai.py`, new — `handle_assessment_narrative(db, job)`: re-fetches
  `AssessmentResult` by `job.payload["assessment_id"]` (benign no-op if missing — e.g. a stale job
  after a hard-deleted assessment), fetches the `Startup` for `industry`/`stage`, calls
  `get_llm_client().complete(...)`, writes `result.narrative = text.strip()`, `db.flush()` (not
  `db.commit()` — the runner owns the transaction boundary, same convention as every other handler
  in `app/worker/handlers/`). Registers itself via `register_handler("ai.assessment.narrative", ...)`
  at import time.
- `app/services/assessment/service.py:219` — `complete_assessment` now enqueues
  `ai.assessment.narrative` immediately after `roadmap.replan`, same `job_payload`
  (`{startup_id, assessment_id}`), same transaction.

**This task (Task 4)**
- `e2e/test_ai_assessment_narrative.py`, new — see Verification below.
- `scripts/e2e_run.sh` — added `export LLM_PROVIDER="stub"`.
- `docs/fe-integration-guide-ai-assessment-narrative.md`, new.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 03 moved from "not started" to "open"; new
  Module 03 section added.
- Pre-existing formatting drift fixed as part of this task's full CI reproduction (mechanical only,
  no logic change — same "final task cleans up the slice's drift" precedent as
  `docs/sop/2026-09-18-notifications-scheduler.md`'s Task 7): `black` reformatted
  `app/core/config.py` (comment alignment after Task 1's `LLM_*` block) and
  `tests/platform/test_llm.py` (Task 2).

**Errors / API surface** — none new. No new route, no new status code, no new error `code`. The
only externally-visible change is that `AssessmentResult.narrative` (already returned by
`POST /assessments/{id}/complete` and `GET /assessments/{id}`) now changes value asynchronously
after completion — see the FE guide's §0/§1 for the exact mechanics and the pre-existing
flat-vs-nested field location this slice makes more consequential.

## Verification

**Per-task unit verification (Tasks 1–3, already green before this task):**
- `tests/core/test_config_llm.py` — `LLM_*` defaults asserted via `Settings.model_fields[...]
  .default` (env-independent — fixed in `5aea9bb` after review caught the original version reading
  the live settings singleton, which would fail on a dev machine with a `.env` override).
- `tests/platform/test_llm.py` — `StubLLMClient` returns the fixed string; `OpenAILLMClient` sends
  `max_completion_tokens` (not `max_tokens`), omits `temperature`, uses `LLM_BASE_URL` as a full
  base including `/v1` with no double-`/v1`; fail-loud on missing key / non-2xx / transport error /
  unparseable body / empty completion; `get_llm_client()` factory dispatch + unknown-provider error.
- `tests/services/assessment/test_narrative.py` — prompt shape, data minimization (no PII).
- `tests/worker/test_ai_handler.py` — overwrites narrative with stub output; no-op when result
  missing; fails loud when the LLM errors (propagates `RuntimeError`, does not write a fallback).

**Task 4 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests e2e` | ✅ pass (2 pre-existing files reformatted first — see "What's involved") |
| Import order | `poetry run isort --check-only app tests e2e` | ✅ pass |
| Lint | `poetry run ruff check app tests e2e` | ✅ pass |
| Types | `poetry run mypy app` | ✅ pass — no issues in 155 source files |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ pass — 9.89/10 (unchanged from Slice 3's last full sweep; no new findings in AI/config code) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ pass, exit 0, no findings (only informational nosec/comment-parser warnings, pre-existing) |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ pass — **1216 passed, 97.70% coverage** (≥ 95% floor) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0025_roadmap_milestone_due_idx (head)`, unchanged from `develop` |
| Live E2E | `./scripts/e2e_run.sh` | ✅ **42 passed** (41 pre-existing + 1 new) |

**`e2e/test_ai_assessment_narrative.py::test_assessment_narrative_is_ai_rewritten`** — a founder
onboards → starts an assessment → walks the adaptive `next-question`/`answers` loop to completion
(mirrors `e2e/test_assessment.py` exactly) → `POST /assessments/{id}/complete` returns the
templated narrative (captured, `complete_templated.json`) → the worker is drained in-process
(`runner.run_once` looped, mirrors `e2e/test_notifications_email.py::_drain`, with
`app.worker.handlers.ai` imported inside the drain function so the handler is registered in the TEST
process, not just the server process) → `GET /assessments/{id}` now returns
`data.result.narrative` starting with `"[stub-llm]"` (captured, `result_ai_narrative.json`),
proving enqueue → job claim → LLM call → persisted overwrite end to end over real HTTP with a real
Postgres-backed worker drain, zero network calls.

**Make-or-break interface fact, called out explicitly (same shape as the notifications-scheduler
SOP's equivalent note):** `scripts/e2e_run.sh` exports `LLM_PROVIDER=stub` for BOTH the spawned
uvicorn server process and the pytest process that runs afterward in the same shell — without it,
the pytest-process worker drain would call `get_llm_client()` under the settings singleton's
`LLM_PROVIDER=openai` default, and `OpenAILLMClient.complete()` would raise `RuntimeError` (empty
`LLM_API_KEY`) the moment the job handler ran, failing the drain instead of deterministically
producing `"[stub-llm]..."`.

**Exact JSON path to the narrative, confirmed live (not guessed) — and the field-nesting trap it
exposes:** `POST /assessments/{id}/complete` returns it **flat** at `data.narrative`;
`GET /assessments/{id}` returns it **nested** at `data.result.narrative`. Both captured verbatim in
`e2e/_captures/ai_assessment_narrative/{complete_templated,result_ai_narrative}.json` and documented
in the FE guide's §1.

## Operate / roll back

**New deploy-time requirement: none.** The `ai.assessment.narrative` job runs inside the existing
`worker` process (Module 20 Slice 2) — no new container, no new health check.

**New/changed config (all have safe defaults; only override if the defaults are wrong for
production):**
- `LLM_PROVIDER` (default `openai`) — **must** have a real `LLM_API_KEY` set in production, or every
  `ai.assessment.narrative` job will fail loud and exhaust its retries, leaving every completed
  assessment's narrative permanently templated (a silent-but-benign degradation — the founder never
  sees an error, they just never get the AI upgrade). Set to `stub` only for local dev/CI/e2e.
- `LLM_API_KEY` (default empty) — required when `LLM_PROVIDER=openai`. Never logged (confirmed —
  `OpenAILLMClient.complete()`'s only reference to it is inside the `Authorization` header it
  builds for `httpx.post`).
- `LLM_MODEL` (default `gpt-5.6-luna`) — swapping models on an OpenAI-compatible endpoint that does
  NOT share `gpt-5.6-luna`'s `max_completion_tokens`/no-`temperature` restrictions may need
  `OpenAILLMClient` revisited — those two behaviors were confirmed against THIS specific model, not
  derived from the general Chat Completions spec.
- `LLM_BASE_URL` (default empty → `https://api.openai.com/v1`) — see "How" above for the
  incl.-`/v1` convention.
- `LLM_TIMEOUT` (default `60` seconds) — per-call HTTP timeout; LLM latency is meaningfully higher
  than a normal API request, budget worker `WORKER_POLL_INTERVAL`/batch sizing accordingly if this
  is raised.
- `LLM_MAX_TOKENS` (default `800`) — output cap for the assessment narrative specifically (passed
  explicitly by `handle_assessment_narrative`); a future second consumer with different length needs
  would pass its own value to `complete()` rather than relying on this default meaning "right for
  every consumer."
- No new secret file, no new `.env.staging.enc`/`.env.production.enc` key beyond `LLM_API_KEY` (not
  yet present in either encrypted env — see Follow-ups; the "Deferred follow-ups" section of the
  checklist already tracks that neither encrypted env file exists yet, a pre-existing gap this slice
  does not close).

**Rollback:** revert this slice's commits as a unit (`f38f2ec..`this commit`` on `develop`, or the
whole branch if not yet merged). No migration to downgrade. The only persisted side effect is
`AssessmentResult.narrative` values that were overwritten with AI text for assessments completed
while this slice was live — those rows are NOT automatically reverted to templated text by rolling
back the code (the templated value was never retained once overwritten); this is judged acceptable
because the AI narrative is, by design, a strictly-better replacement of the same field, not a
distinct piece of state a rollback needs to undo.

## Follow-ups

**Structured output is a later slice, not this one.** This slice ships free-text narrative
generation only. Module 08's AI Business Plan Generator (§08.11, now unblocked ON THE SEAM by this
slice) still needs structured (JSON-shaped) LLM output for plan sections — a different `complete()`
contract or a new method on `LLMClient`, deliberately not built here so this slice could land the
simplest possible working consumer first.

**No Anthropic (or other non-OpenAI-compatible) implementation.** `get_llm_client()` supports
`"openai"` and `"stub"` only; `.env.example`'s comment already anticipates `anthropic` as a future
`LLM_PROVIDER` value (uses `claude-3-5-sonnet-latest` as its example model) but no such client class
exists yet.

**Every other deferred-to-Module-03 AI consumer remains unbuilt.** This slice proves the seam with
exactly one real consumer; the onboarding AI panel (`app/platform/ai.py::StubAIPanel`, still a
canned string), Mission's reason line, Roadmap's replan rationale, Health Score's recommendation
reasons, Learning's recommendations, Validation Hub's insight synthesizer, and Business Builder's
`ai-fill` jobs (canvas + typed records) are all still on their pre-Module-03 fallback behavior —
each is its own future slice, now unblocked on infrastructure but not started.

**Per-workspace budgets/rate limiting do not exist.** Nothing in this slice caps how many LLM calls
a single workspace can trigger (each assessment completion is one call; a founder could in principle
complete many assessments). Not a concern yet at this scale, but worth tracking before a
higher-volume consumer (e.g. a chat-style AI panel) is built on the same seam.

**`StubAIPanel` (`app/platform/ai.py`) is a separate, older stub** from Module 01.6 onboarding — not
touched or retired by this slice. It has its own `AIPanel` Protocol, unrelated to `LLMClient`; a
future slice could either replace it with a real `LLMClient`-backed implementation or retire it if
onboarding's AI panel is redesigned around this seam directly.

**No structured "AI enrichment failed / still templated" signal.** As documented in the FE guide's
§0, there is no field distinguishing a templated narrative from an AI one, and no way for the FE (or
an operator) to tell, without checking worker logs, whether a specific assessment's job succeeded,
is still queued, or exhausted its retries. If this becomes an operational pain point (e.g. wanting
to alert on a spike of failed `ai.assessment.narrative` jobs), that needs the worker-DLQ/alerting
work the notifications-scheduler SOP's Follow-ups already flagged as a future need for `scheduled.*`
jobs — the same gap, now shared by a second job type.
