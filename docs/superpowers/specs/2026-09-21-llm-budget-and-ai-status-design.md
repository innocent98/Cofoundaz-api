# Module 03 — Per-workspace LLM daily token budget + AI status signal (design)

**Status:** approved-for-planning
**Date:** 2026-09-21
**Module:** 03 (AI Co-Founder) infrastructure — closes two of the three Slice-1-deferred infra items.
**Depends on:** the LLM seam (`app/platform/llm.py`) + the job worker + all Module 03 AI consumers — shipped.

## Goal

Two coupled guardrails over the AI features:
1. **Per-workspace daily token budget** — cap each startup's LLM token spend per day (default 15,000,
   config-tunable; `≤ 0` = unlimited kill-switch). When a startup is over budget, AI enrichments are
   **skipped** (the templated fallback stays), never errored.
2. **AI status signal** — a `GET /ai/status` endpoint reporting the workspace's token usage vs budget
   (so the FE/operator can see when personalization is paused) plus recently-failed enrichment jobs.

Both are **provider-independent** (they meter usage and expose status regardless of OpenAI vs any
future provider). Also drops the third deferred item — the non-OpenAI provider impl — as **won't-do
(OpenAI-only, owner decision 2026-09-21)**.

## Why

- No cost/abuse guardrail exists today: every AI job calls `get_llm_client().complete*()` with no
  per-tenant cap (~9 call sites). One workspace can drive unbounded OpenAI spend.
- The async-upgrade pattern gives no visibility into whether a field is still templated because a job
  is pending, failed, or was skipped for budget. `/ai/status` surfaces that at the workspace level
  without threading a status field through all 8 consumer response shapes.

## Scope

### In scope
1. **`llm_usage_daily` table** (+ migration `0030`) — per `(startup_id, usage_date)` token counter.
2. **Config** — `LLM_DAILY_TOKEN_BUDGET: int = 15000` (`≤ 0` = unlimited).
3. **Seam usage plumbing** — `OpenAILLMClient` records `last_usage_tokens` from the response's
   `usage.total_tokens`; `StubLLMClient.last_usage_tokens = 0`.
4. **Metering helper** (`app/platform/llm_budget.py`) — `over_budget`, `debit`, `metered_complete`,
   `metered_complete_json`.
5. **Enforce at every AI call site** — the ~8 handlers in `app/worker/handlers/ai.py` use
   `metered_complete*` and skip-on-`None`; the business-plan handler (`plan.py`) uses check-once + per-section debit.
6. **`GET /ai/status`** endpoint.
7. Tests (unit + one live e2e for `/ai/status`), FE guide, SOP, checklist (mark the two items done,
   drop the provider item as won't-do). No consumer response shape changes.

### Out of scope (deferred)
- Per-field `pending|ready|failed` status on each consumer (workspace-level status is v1).
- A per-consumer terminal state when skipped for budget (e.g. dashboard briefing stays `generating`;
  the `/ai/status` `over_budget` flag explains it) — follow-up.
- Rate-limiting by calls/minute (this slice is token-budget only; the "rate-limit" concern is covered
  in spirit by the daily token cap for v1).
- Non-OpenAI provider implementation — **dropped, won't-do (OpenAI-only)**.

## Architecture

### 1. Data model — `app/db/models/llm_usage.py` (new) + migration

`LlmUsageDaily(UUIDMixin, TimestampMixin, Base)`:
- `startup_id: Mapped[uuid.UUID]` — FK `startups.id` `ondelete="CASCADE"`, `index=True`.
- `usage_date: Mapped[date]`.
- `tokens_used: Mapped[int]` — `default=0`, `server_default="0"`, non-null.
- `__table_args__ = (UniqueConstraint("startup_id", "usage_date", name="uq_llm_usage_startup_date"),)`.

Registered in `app/db/models/__init__.py`. Migration `0030_llm_usage_daily` (`--autogenerate`, renumber;
down_revision `0029_startup_profile_ai_panel`). Single linear head.

`usage_date` = `datetime.now(UTC).date()` (UTC calendar day; resets at UTC midnight — simplest correct
window for v1).

### 2. Config — `app/core/config.py`

`LLM_DAILY_TOKEN_BUDGET: int = 15000`. Semantics: a startup may spend up to this many total tokens per
UTC day; `≤ 0` disables metering entirely (unlimited — the kill-switch, and what tests that don't care
about budget can set).

### 3. Seam usage plumbing — `app/platform/llm.py`

- `OpenAILLMClient`: after `_post_chat`, set `self.last_usage_tokens = int(body.get("usage", {}).get("total_tokens", 0) or 0)` in both `complete` and `complete_json` (the body already carries `usage`). Initialize `last_usage_tokens: int = 0` on the instance.
- `StubLLMClient`: `last_usage_tokens: int = 0` (so stub mode never accrues budget → all existing
  tests/e2e stay green).
- No change to the `LLMClient` Protocol method signatures (existing callers unaffected); the attribute
  is read by the metering helper, which constructs the client itself.

### 4. Metering helper — `app/platform/llm_budget.py` (new)

```python
def over_budget(db, startup_id) -> bool:
    budget = settings.LLM_DAILY_TOKEN_BUDGET
    if budget <= 0:
        return False  # unlimited
    used = db.execute(select(LlmUsageDaily.tokens_used).where(
        startup_id == ..., usage_date == today_utc())).scalar_one_or_none() or 0
    return used >= budget

def debit(db, startup_id, tokens) -> None:
    # atomic upsert-increment; no lost updates under concurrent workers
    db.execute(pg_insert(LlmUsageDaily).values(startup_id=..., usage_date=today_utc(),
        tokens_used=tokens).on_conflict_do_update(
        index_elements=["startup_id", "usage_date"],
        set_={"tokens_used": LlmUsageDaily.tokens_used + tokens}))
    db.flush()

def metered_complete(db, startup_id, messages, *, max_tokens) -> str | None:
    if over_budget(db, startup_id):
        return None                      # skip — caller keeps templated value
    client = get_llm_client()
    text = client.complete(messages, max_tokens=max_tokens)
    debit(db, startup_id, getattr(client, "last_usage_tokens", 0))
    return text

def metered_complete_json(db, startup_id, messages, *, schema, max_tokens) -> dict | None:
    ... parallel ...
```
Boundary rule: the check is before the call, the debit is after actual usage — so the one call that
crosses the line is allowed (acceptable for a safety rail). No `db.commit` (runner owns the txn).

### 5. Enforcement at the call sites

- **The 8 handlers in `app/worker/handlers/ai.py`**: replace `get_llm_client().complete*(...)` with
  `metered_complete*(db, startup_id, ...)` and, on `None`, `return` (skip — the templated value written
  at enqueue/gen time remains; no status flip, no error). `startup_id` is already in-scope in each
  handler (payload or the loaded startup/entity).
- **The business-plan handler `app/worker/handlers/plan.py`** (10 per-section `complete` calls, the
  biggest spender): call `over_budget(db, startup_id)` ONCE before the section loop; if over, no-op
  (leave the plan un-generated for a later retry — no partial plan). If under, generate all sections,
  each via `metered_complete` with the section's existing templated/heading fallback if it returns
  `None` (only possible if a concurrent job crossed the line mid-plan — rare, and the plan stays whole),
  debiting actual usage per section.

### 6. Status endpoint — `GET /ai/status`

New router `app/api/v1/endpoints/ai.py` mounted under `/api/v1/ai`. `require_workspace` + verified user
(same auth as other tenant endpoints). Returns `success_response`:
```json
{
  "tokens_used_today": 4213,
  "daily_budget": 15000,
  "over_budget": false,
  "resets_at": "2026-09-22T00:00:00+00:00",
  "recent_enrichment_failures": [
    {"type": "ai.dashboard.briefing", "failed_at": "2026-09-21T14:02:11+00:00"}
  ]
}
```
- `tokens_used_today` from `llm_usage_daily` (0 if no row).
- `daily_budget` = `LLM_DAILY_TOKEN_BUDGET` (report `null`/`0` semantics for unlimited: if `≤ 0`,
  `daily_budget: null`, `over_budget: false`).
- `over_budget` = `over_budget(db, startup_id)`.
- `resets_at` = next UTC midnight ISO.
- `recent_enrichment_failures` = the startup's `Job` rows where `type` is an AI-enrichment type and
  `status == failed`, most recent first, capped (e.g. last 20 / last 24h). AI-enrichment types:
  `type.startswith("ai.")` OR `type.endswith(".ai_fill")` OR `type == "business.plan.generate"`
  (a small `is_ai_enrichment_job(type)` predicate).

## Error handling / concurrency

- Atomic `ON CONFLICT DO UPDATE … + tokens` → no lost updates when two workers debit the same startup/day.
- The pre-call `over_budget` read can be slightly stale under concurrent workers → at most a small
  over-spend on the boundary call; acceptable for a safety rail.
- Skip path never raises and never marks a job failed — the job succeeds as a no-op, the templated
  value stays; `over_budget` on `/ai/status` is the signal.
- Unlimited (`≤ 0`) short-circuits before any DB read.
- PII: metering stores only counts; `/ai/status` exposes only the caller's own workspace usage.

## Testing

- **Unit — ledger**: `debit` upserts then increments (two debits sum); `over_budget` false under
  budget, true at/over, false when `LLM_DAILY_TOKEN_BUDGET ≤ 0`.
- **Unit — metered helper** (fake usage-reporting client): under budget → returns text + debits actual
  tokens; over budget → returns `None`, no client call, no debit.
- **Unit — handler skip**: with the ledger seeded over budget, an AI handler no-ops (field unchanged);
  under budget it enriches and the ledger increments.
- **Unit — status shape**: `/ai/status` returns the fields; `over_budget` reflects the ledger; failures
  list picks up a `failed` AI job and excludes non-AI/failed-non-AI jobs; unlimited → `daily_budget:null`.
- **Unit — seam usage**: `OpenAILLMClient.last_usage_tokens` parsed from a fake body with `usage`;
  stub = 0.
- **E2E (stub)**: `GET /ai/status` for an onboarded workspace returns the shape with `tokens_used_today: 0`
  (stub debits 0) and `over_budget: false`. Capture it. (Existing AI e2e stay green — stub accrues 0.)
- DB-clean unit tests; migration round-trip/drift; coverage ≥ 95%.

## Security & privacy

`/ai/status` is workspace-scoped (a caller sees only their own usage/failures). No new PII. Metering
stores only integer counts. `LLM_API_KEY` unchanged, never logged.

## FE impact (integration guide)

New `docs/fe-integration-guide-ai-status.md`: `GET /ai/status` shape (verbatim from the capture); when
`over_budget` is true, AI personalization is paused until `resets_at` and users will see the templated
fallback text on new enrichments (existing AI text is unaffected); `recent_enrichment_failures` is for
surfacing/observability. Note the daily budget is a server config.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment.
- Reproduce CI locally & green before push: black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%,
  **Migrations (round-trip + drift) — one new migration `0030`, single head**, e2e.
- DB-clean unit tests; worker no-commit convention (helpers/handlers end with `db.flush()`); seam
  fail-loud on real LLM errors (metering does not swallow them — only budget-skip returns `None`).
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live captures.

## Follow-ups

Per-field enrichment status; a per-consumer terminal state on budget-skip (dashboard "stuck generating");
call-rate limiting (calls/min); making the budget window timezone-aware. **Provider impl: won't-do
(OpenAI-only).**
