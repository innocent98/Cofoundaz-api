# SOP — Business Builder backend, PRD completion slice (c): plan flow, revenue model, fit-lines

**What shipped (one line):** The full PRD 08.11 plan flow (options, readiness checklist, "draft missing sections with
AI", live stage progress, a real failed state, regenerate one section), the PRD 08.8 revenue model (projection +
"Send to financial model" feeding the Finance 3-statement model), and PRD 08.5 Value Proposition fit-lines.
Branch `feat/business-builder-prd-c`, stacked on slice (b) (#140).

## Why
Gap rows 12, 15 and 17 (`cofoundaz/docs/checklist/business-builder-prd-gaps.md`). The plan screen was one button with
no options or progress. A plan could stay `generating` forever: over-budget runs left it there on purpose, and LLM
failures retried and then stopped with the plan untouched. Revenue streams had no projection and no route into
Finance. Value Prop had nowhere to store fit-lines.

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| Plan `options` {audience, length} shape every section prompt (audience framing, word budget) | PRD 08.11 options; one generation path | Separate prompt sets per audience |
| Progress written per section from a **separate short transaction** (`SessionLocal`), only while `generating` | The runner commits the job's transaction only at the end; a polling FE needs to see stages as they happen. Best effort: progress never fails a plan | Commit inside the handler (breaks the runner's transaction ownership); time-based fake captions |
| Failed state: over budget → `failed/over_budget`; an LLM error re-raises (runner retries with backoff) until the **last attempt**, then `failed/generation_failed` | A plan can't stay `generating` forever, and transient errors still retry | Failing on the first error; leaving it `generating` |
| Section regenerate rewrites **one** Document section matched by heading, then bumps the doc version; `section_missing` if the founder removed it | Keeps every other edit; versioning makes a concurrent editor reload instead of overwriting | Regenerating the whole plan |
| Readiness: canvas complete → ready, started → partial, else missing; records ≥1 → ready. Draft-missing reuses the existing fill jobs (empty blocks / empty kinds only) | No new AI paths; co-pilot rule kept | A new plan-specific fill |
| **D-BB-5 revised:** "Send to financial model" stores the streams as `finance_runway_settings.revenue_drivers`; the 3-statement model uses them as starting revenue **only when actual revenue is 0**, and records `revenue_source` + `revenue_0_minor` in its assumptions | PRD 12.6 says model revenue drivers come from Module 08. The runway has no revenue assumption (revenue comes from transactions), so the original plan to write runway assumptions didn't fit the code | Overriding real actuals; a new forecast table |
| Projection: per-stream monthly compounding in major units; growth defaults to the runway MoM assumption | Matches the stream's own units; one growth source of truth | Engine-based projection (needs costs and cash) |
| Fit-lines in `business_canvases.meta.fit_links`, matched by item text, pruned on read; saving doesn't bump the canvas version | Lines survive reorders; deleted or renamed items never leave dangling lines; autosave isn't disturbed | Index-based links; a links table |

## What's involved
**New:** `alembic/versions/0054_business_plan_options.py` (`business_plans.options/progress/error`,
`business_canvases.meta`, `finance_runway_settings.revenue_drivers`), `app/services/business/plan_tools.py`,
`tests/api/test_business_prd_c.py`, `e2e/_captures/business_prd_c/`, `e2e/_captures/business_prd_c_live/`.
**Changed:** `app/worker/handlers/plan.py` (options, progress, failed, `handle_plan_section`),
`app/services/business/plan_defs.py` (captions, options), `plan_prompt.py` (options), `service.py` (`fit_links`),
`app/api/v1/endpoints/business.py` (generate body, plan GET, readiness, draft-missing, regenerate, projection,
send-to-finance, fit), `app/schemas/business.py`, `app/db/models/business.py`, `app/db/models/finance_runway.py`,
`app/worker/handlers/finance_model.py` + `app/services/finance/model_prompt.py` (drivers), `e2e/test_business_builder.py`
(slice (c) journey; the drain now registers the plan and finance-model handlers),
`tests/worker/test_plan_handler.py` (over budget now ends `failed`).

## Verification
Fresh-DB pytest under UTC: 2363 passed, 97.37% coverage; semgrep (CI configs) clean; pylint 9.88. Static gates, alembic gates
(scratch DB) and e2e 73/73 all pass. Real model on the local stack: a full plan run advanced through all 10 stage
captions (about 3s each) and completed (`e2e/_captures/business_prd_c_live/`).

## Operate / roll back
`alembic upgrade head` (0054, metadata-only), then **restart the worker** (new handler, changed plan and finance-model
handlers). Roll back: revert, then `alembic downgrade 0053_comment_anchor`.

## Follow-ups
- The Finance model page should flag `revenue_source: "business_builder"` (the FE guide §17.2 says so). It belongs with
  the Finance page's PRD walk.
- PRD 24.2 "job-cost confirm" for big jobs (plan, financial model) waits on Billing (blocked on a provider decision).
