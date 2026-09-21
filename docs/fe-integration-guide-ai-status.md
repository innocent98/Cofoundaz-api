# FE Integration Guide — AI Status (`GET /ai/status`)

The response body below is pasted **verbatim** from a live capture taken by
`e2e/test_ai_status.py::test_ai_status` running against a real server (`scripts/e2e_run.sh`) — see
`e2e/_captures/ai_status/status.json`. Nothing here is retyped from the schema, the endpoint code,
or memory.

This is a **new endpoint**, not an extension of an existing one: `GET /api/v1/ai/status`
(`app/api/v1/endpoints/ai.py::ai_status`). It reports the workspace's LLM token usage against the
configured daily budget, plus recent failed AI-enrichment jobs — so the FE can surface
budget/enrichment status without querying the `llm_usage_daily` ledger or the jobs table directly.

---

## 0. The one thing the FE must know

When `data.over_budget` is `true`, AI personalization is **paused workspace-wide** until
`data.resets_at` (next UTC midnight): every AI-enrichment job that would otherwise call the LLM
(assessment narrative, canvas/records `ai_fill`, mission reason, health recommendations, dashboard
briefing, roadmap rationale, onboarding panel, business-plan generation) silently skips its LLM
call and **keeps whatever templated/default value it already has**. This is not an error state —
no job fails, no field flips to a "failed" status, nothing 4xxs. It's a quiet degrade: the founder
keeps working with deterministic, already-good-enough copy instead of AI-upgraded copy, and
enrichment resumes automatically at `resets_at` with no action needed from the FE or the founder.

**Existing AI-authored text already on a record is never touched or reverted** — `over_budget`
only affects *new* enrichment attempts going forward, not anything already written.

---

## 1. Auth & request shape

Same tenant-scoped convention as every other workspace endpoint:

```
GET /api/v1/ai/status
Authorization: Bearer <access_token>
X-Workspace-Id: <workspace/startup id>
```

No query params, no request body. `require_workspace` (the same dependency every other
workspace-scoped route uses) resolves the caller's `Membership` from `X-Workspace-Id`; a missing
or invalid header behaves exactly as it does on every other tenant endpoint (401/403 per the
existing auth/tenancy contract — this endpoint introduces no new error shape of its own).

---

## 2. Live capture

`e2e/_captures/ai_status/status.json` — captured against a **fresh** workspace under
`LLM_PROVIDER=stub` (no AI job has run yet, so usage is 0 and nothing is over budget):

```json
{
  "data": {
    "tokens_used_today": 0,
    "daily_budget": 15000,
    "over_budget": false,
    "resets_at": "2026-09-22T00:00:00+00:00",
    "recent_enrichment_failures": []
  },
  "meta": null
}
```

---

## 3. Field reference

| Field | Type | Meaning |
|---|---|---|
| `tokens_used_today` | `int` | Total LLM tokens this workspace's `startup_id` has consumed since UTC midnight today, summed across every AI job type (assessment narrative, ai-fill, mission reason, health recommendations, dashboard briefing, roadmap rationale, onboarding panel, business-plan generation). Read from the `llm_usage_daily` ledger (`(startup_id, usage_date)` unique row); `0` if no row exists yet for today (fresh workspace, or a workspace that simply hasn't triggered any AI job today). |
| `daily_budget` | `int \| null` | **Server config**, not a per-workspace setting — the same `LLM_DAILY_TOKEN_BUDGET` value applies to every workspace. `15000` by default. **`null` means unlimited** (the operator has set `LLM_DAILY_TOKEN_BUDGET <= 0`, the documented kill-switch) — treat `null` as "no cap to render," not as "budget unknown." |
| `over_budget` | `bool` | `true` when `tokens_used_today >= daily_budget` (only ever `true` if `daily_budget` is non-null — an unlimited budget is never "over"). This is a **live re-check on every request**, computed the same way the worker checks it before each LLM call — not a cached flag, so it can flip from `true` back to `false` before `resets_at` if the operator raises the budget or clears the ledger, though in normal operation it only flips at UTC midnight. |
| `resets_at` | `string` (ISO 8601, UTC) | The next UTC midnight — when the daily usage window (and therefore `over_budget`, if currently `true`) resets. Always present, even when not over budget (useful for "budget resets in N hours" copy regardless of current state). **Not workspace-timezone-aware** — this is a fixed UTC-midnight boundary; a founder in a non-UTC timezone will see the reset happen at a local time that shifts with DST, not at their local midnight. |
| `recent_enrichment_failures` | `array<{type, failed_at}>` | **Observability, not an action queue.** Up to 20 of the startup's most recent *failed* AI-enrichment job rows (job `status == failed`, job `type` matching an AI-enrichment pattern — `ai.*`, `*.ai_fill`, or `business.plan.generate`), newest first. `type` is the raw job type string (e.g. `"ai.assessment.narrative"`, `"business.canvas.ai_fill"`); `failed_at` is the job's `updated_at` timestamp (ISO 8601, UTC), or `null` if somehow unset. An empty array (`[]`) is the common case — it means "no known failures," not "nothing has ever run." |

**On `over_budget` and `recent_enrichment_failures` together:** these are two *different* signals
that a founder isn't getting AI-upgraded content. `over_budget: true` explains "no new AI
enrichment will happen until `resets_at`, on purpose, by design" (a soft, expected throttle).
`recent_enrichment_failures` explains "these specific jobs tried and hard-failed" (LLM timeout,
non-2xx, bad key, etc — an operator-facing signal, not a business condition). A workspace can have
entries in `recent_enrichment_failures` while `over_budget` is `false`, and vice versa — they are
independent.

---

## 4. Field-nesting notes

No nesting traps here — this is a flat, purpose-built response with no field shared with another
endpoint at a different depth. All 5 fields are direct children of `data`. Standard envelope:
`{"data": {...}, "meta": null}`.

---

## 5. Suggested FE handling

- **Budget banner:** when `over_budget` is `true`, show a non-alarming "AI personalization is
  paused until `resets_at` (converted to the viewer's local time for display) — your data is
  never affected" message. Do not present this as an error or a degraded/broken state.
- **Do not poll this endpoint in a tight loop.** Usage only changes when an AI job runs (seconds
  to minutes after a triggering action), and the reset boundary is a fixed UTC midnight — fetch on
  a relevant screen load (e.g. a settings/usage page) or on a natural navigation, not on an
  interval.
- **`daily_budget: null` → render "unlimited"**, not a blank/zero/error state, and skip any
  usage-vs-budget progress bar (there's nothing to divide by).
- **`recent_enrichment_failures` is best shown to an admin/operator-facing surface, if at all** —
  it's raw job-type strings, not founder-friendly copy. It is not a live action queue (nothing
  auto-retries from this list), and it has a known gap (see Verification table, row 6) where a
  recent AI failure can be excluded from `recent_enrichment_failures` if the workspace has 200+
  more-recent failures of any other (non-AI) job type in between.
- **Never infer "this specific record is still templated" from this endpoint.** Neither
  `over_budget` nor `recent_enrichment_failures` tells you which individual records (a mission
  reason, a canvas block, a health recommendation) are still on their templated/default value vs.
  AI-upgraded — this endpoint is a workspace-wide aggregate only. See each feature's own FE guide
  for that feature's specific templated-vs-AI-authored behavior.

---

## 6. Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `GET /ai/status` returns 200 with all 5 fields for an authenticated, workspace-scoped caller | ✅ | `e2e/test_ai_status.py::test_ai_status`, `status.json` |
| Fresh workspace (no AI job has run) → `tokens_used_today: 0`, `over_budget: false` | ✅ | `status.json` |
| `daily_budget` reflects the live `LLM_DAILY_TOKEN_BUDGET` config value (`15000` in this run) | ✅ | `status.json` |
| `resets_at` is next UTC midnight, ISO 8601 | ✅ | `status.json` (`"2026-09-22T00:00:00+00:00"`, captured on 2026-09-21) |
| `recent_enrichment_failures: []` when no AI job has ever run for the workspace | ✅ | `status.json` |
| `daily_budget: null` when `LLM_DAILY_TOKEN_BUDGET <= 0` (unlimited) | ⚠️ unit only — the live e2e run pins `LLM_DAILY_TOKEN_BUDGET` to its default (`15000`) via `scripts/e2e_run.sh`; toggling the kill-switch live would require a separate server process with different env | `app/api/v1/endpoints/ai.py:42` (`budget if budget > 0 else None`), `tests/api/test_ai_status.py` |
| `over_budget: true` once `tokens_used_today >= daily_budget`, and enrichment jobs skip-and-keep-templated in that state | ⚠️ unit only — forcing real token usage past a budget in a live e2e run would require either a real LLM call (no network in e2e, `LLM_PROVIDER=stub` debits 0 tokens) or seeding the ledger directly, neither of which this journey does | `tests/platform/test_llm_budget.py`, `tests/worker/test_ai_budget_enforcement.py` |
| A failed AI-enrichment job appears in `recent_enrichment_failures` with the correct `{type, failed_at}` shape | ⚠️ unit only — not reachable from a normal live journey without forcing a job failure, which would require breaking the shared e2e process's `LLM_PROVIDER=stub` guarantee for every other e2e test | `tests/api/test_ai_status.py` |
| Non-AI failed jobs (e.g. an email delivery failure) are excluded from `recent_enrichment_failures` | ⚠️ unit only | `tests/api/test_ai_status.py` (`is_ai_enrichment_job` filtering) |
| **Known edge case, not fixed by this slice:** the endpoint fetches only the 200 most-recent failed jobs of *any* type before filtering to AI-enrichment ones and capping at 20 — a workspace with 200+ more-recent non-AI failures can have an older AI failure silently excluded from `recent_enrichment_failures`, even though it would otherwise rank in the newest 20 AI failures | N/A (design gap, not a tested behaviour) | `app/api/v1/endpoints/ai.py:45-56` (`.limit(200)` pre-filter); see SOP Follow-ups |

The ⚠️ rows are genuine gaps in this one live journey — toggling the budget kill-switch, forcing a
real token debit past budget, or forcing a job to fail are not reachable from a normal signup →
onboard → `GET /ai/status` HTTP walk without either running a second server process with different
config or fabricating job/ledger rows directly — both are covered by passing unit tests at the
cited paths instead of an invented live example.
