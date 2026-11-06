# Investor Readiness: financial narrative check (slice C)

**What shipped:** PRD 15.4. A founder keeps one financial narrative draft per workspace and presses "Check": the model reads the numbers out of the latest reviewed pitch deck and the draft, and code compares them with what Cofoundaz knows (books, financial model, team, Funding round target), producing findings in the PRD copy ("Deck says ₦2M MRR; your model says ₦1.6M. Fix the number or the story."). It feeds two score signals (Financials "Use of funds defined", Story "Narrative consistent with the numbers") and the History log. API only; FE guide section 14 of `docs/fe-integration-guide-investor-readiness.md`. Branch `feature/readiness-narrative`, PR: see the branch. Migration **`0103_readiness_narrative`** (chained on the newest head at push; 0101/0102 were reserved for Marketplace C and Funding D).

**Why / decisions:** spec §3.4 ("LLM extracts, code compares") and D-IR-4 (founder runs and edits, team member reads, everyone else 403). Manual button only, metered against the daily AI budget; no AI on page load.

**How**

| Topic | Decision |
|---|---|
| Draft | `readiness_narrative`, unique `startup_id`, `body` up to 20,000 chars, `updated_by_id`. `PUT` replaces the whole text (autosave, last write wins) under an advisory lock. |
| Check job | `POST /narrative/check` queues `ai.readiness.narrative_check` (the `ai.` prefix puts it under the budget gate). **One** `metered_complete_json` call (the spec allowed two) with the deck text (re-extracted from the reviewed PDF with slice B's `extract_pages`, capped 14k chars) and the draft (capped 12k chars), each in an "untrusted" fence. Output capped at 1,500 tokens. |
| Model can't invent | Every claim must name a known metric, quote words found verbatim in its source (whitespace, case and the LLM seam's dash rewrite aside), and its value must be a number written in that quote (k/M/MM/bn handled; counts may also match the bare number, money never). Targets and projections are dropped (a `tense` field plus future-word markers; the raise and runway are exempt). Use-of-funds items need a verbatim quote too. Dropped claims are counted in the job result. |
| Facts | Pure SQL: books (`cash_flow_summary`: 3-month average revenue = MRR, ×12 = ARR, net burn, cash, runway) win over the financial model (month 1 revenue / net income, growth assumption with the founder's overrides; labelled "edited since it was calculated" when stale); active founder + team member count; the open Funding round's `target_minor` as the ask. |
| Comparator | Per metric, 10% tolerance: ≤10% `ok`, 10-25% `medium`, >25% `high`; fact 0 and claim > 0 is high. Deck vs draft disagreeing on one metric is `inconsistent` (high). `unverified` (info, never a mismatch): no fact, different currency, a claimed team larger than the workspace members, a burn claim against zero net burn. |
| Reuse | If the draft hash and the deck review are unchanged since the last finished check, the claims are reused and facts/findings recomputed now: no model call, `tokens_used: 0`, returned at once (`outcome: reused`). A repeat press within 60 s with the facts also unchanged returns the same check (no new row, no forced recompute). |
| Budget | `over_budget` → 429 `AI_BUDGET_EXHAUSTED`; under 10,000 tokens of headroom → 409 `AI_BUDGET_LOW`, both before anything is created. A check already queued/running is returned (`in_progress`), never a second job. Failed calls are billed independently (existing rule). After the paid call nothing may raise: any error marks the check `processing_failed` and keeps the debit (a raise would roll the debit back and retry unbilled). `tokens_used` comes from the call itself (`metered_complete_json(usage=...)`, a new optional out-param). |
| Strict schema | OpenAI strict structured output needs `additionalProperties: false` and every key `required` on every object; the first real-model run 400'd without it. A unit test now walks the schema. |
| Score | `story.narrative` (weight 2, step target 2): 0 never checked, 1 a high finding or nothing comparable, 2 at least one number compared and no high finding (so "We are great." cannot earn full marks). `financials.use_of_funds` (weight 1): 1 when the latest finished check found a quoted use-of-funds item. A Funding round target alone does **not** count (spec allowed it): it says how much, not what for. `readiness.narrative.checked` refreshes the score immediately. History log `narrative_check` rows + "Narrative checked" markers. |
| Stale / dead | `view()` sets `stale` when the draft or the latest reviewed deck changed since the check. A queued/running check whose job is gone is marked `failed: job_failed` on read (conditional UPDATE, same as deck reviews). The draft is re-read when the job runs, so the stored hash matches what was checked. |

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/readiness.py` (`ReadinessNarrative`, `NarrativeCheck`), `alembic/versions/0103_readiness_narrative.py` |
| Service, job | `app/services/readiness/narrative.py`, `app/worker/handlers/readiness.py` (`handle_narrative_check`) |
| API | `app/api/v1/endpoints/readiness.py`, `app/schemas/readiness.py` (`NarrativeIn`): `GET/PUT /readiness/narrative`, `POST /readiness/narrative/check` (10/min), `GET /readiness/narrative/checks/latest` |
| Score, history, triggers | `app/services/readiness/signals.py`, `service.py`, `triggers.py` (event in the refresh and force sets) |
| Shared | `app/platform/llm_budget.py` (`metered_complete_json` optional `usage` list) |

**Verification**

- `tests/services/readiness/test_narrative.py` (54): quote numbers, claim cleaning (invented, misread, unknown, overflow, dashes, targets, formats, funds quotes), comparator bands and copy, facts (books vs model vs funding vs team), draft, create/run/reuse/cooldown, budget gates, failures (provider retry, over budget at run, processing after the paid call keeps the debit), deck file gone, draft cleared, reconcile, stale, score signals, history, strict schema.
- `tests/api/test_readiness_narrative_api.py`: all 7 roles on all 4 routes, full flow, validation, tenancy, 429.
- `tests/test_readiness_migration.py`: 0103 in the chain; downgrade drops both tables, upgrade restores, `alembic check` clean.
- Live journey `e2e/test_readiness_narrative.py` on a scratch stack: scripted captures `e2e/_captures/readiness_narrative/`, and a **real-model** run of the extraction (`NARRATIVE_E2E_REAL_LLM=1`) in `e2e/_captures/readiness_narrative_real/`: 6 claims kept, 1 dropped, the planted ₦2M vs ₦1.6M disagreement found, **1,329 tokens** per check (spec estimate 8k).
- Opus review (permission + AI budget paths): no high findings; 10 medium/low fixed in `b1f33ea2` (see the PR body).

**Operate / roll back:** no env vars. Roll back with `alembic downgrade` to the revision before `0103_readiness_narrative` (drops both tables; drafts are lost) and revert the PR. The worker must be redeployed with the API (new job type).

**Follow-ups**

- Slice B's `REVIEW_SCHEMA` and slice D's `SCORE_SCHEMA` lack `additionalProperties: false` / `required`, so the deck review and Q&A scoring will 400 on the real OpenAI model in strict mode (found by this slice's real run; neither was run on a real model). Fix before the Readiness FE pass.
- No growth fact from the books (only the model's assumption); ARR is books MRR × 12.
- Non-NGN books and USD claims are "unverified", never converted.
- Token cost on a 30-slide deck plus a 20k-char draft not measured (bounded by the 14k + 12k char caps).
