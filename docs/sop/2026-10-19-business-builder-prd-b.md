# SOP — Business Builder backend, PRD completion slice (b): section comments and AI assists

**What shipped (one line):** Comment threads on canvas sections and records; per-section AI assist (append-only);
Mission & Vision "Give me 3 options"; "Generate personas from my validation notes"; and the pricing AI advisor.
Branch `feat/business-builder-prd-b`, stacked on slice (a) (#139).

## Why
Gap rows 8, 9, 11, 13 (AI part) and 14 (`cofoundaz/docs/checklist/business-builder-prd-gaps.md`). Each of these PRD 08
features had no backend: comments only accepted roadmap entities, AI fill could only do a whole artifact, Mission &
Vision had no variant generator, personas ignored Module 09 data, and pricing had no advisor.

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| Reuse `/team/comments` with entity types `business_canvas` / `business_record`, plus a nullable `anchor` (block key) | One thread component app-wide (PRD 23.2); a section is an anchor inside the canvas, not an entity of its own | A Business Builder comments table; section rows as entities |
| Anchor allowed only on `business_canvas`, validated against the canvas's block keys (422 before the entity lookup) | Bad shapes fail fast and the same way every time | Free-text anchors |
| D-BB-4: section AI reuses `business.canvas.ai_fill` with `block` in the payload; list blocks get ≤3 new items deduped case-insensitively; a text block is written only if empty; outcome in `job.result` | Co-pilot rule: never rewrite what the founder wrote; refresh-after-LLM keeps concurrent edits | Replacing the block; a separate job type |
| M&V options and the pricing advisor return results in the job and write nothing | The founder chooses ("Use this", "Apply") through the normal write paths, which keeps history and RBAC in one place | Auto-applying AI output |
| Personas from validation: latest 10 interviews (segment, verdict, notes ≤600 chars, quotes) + assumptions, **no interviewee names**; append, dedupe by name; 422 `NO_VALIDATION_DATA` up front | PII stays out of prompts; a clear error beats an empty job | Sending full interview rows |
| New AI jobs are named `ai.business.*` | `is_ai_enrichment_job` (budget gate, AI usage) recognises the `ai.` prefix | — |

## What's involved
**New:** `app/services/business/ai_assist.py`, `alembic/versions/0053_comment_anchor.py`, `tests/api/test_business_prd_b.py`
(14), `e2e/_captures/business_prd_b/`, `e2e/_captures/business_prd_b_live/` (real model).
**Changed:** `app/db/models/comment.py` (`anchor`), `app/schemas/team.py`, `app/services/team/service.py` (business
entities, anchor checks, list filter), `app/api/v1/endpoints/team.py`, `app/schemas/business.py` (`CanvasAiFill`,
`MissionVisionOptions`, `PricingAdvisorRequest`), `app/api/v1/endpoints/business.py` (ai-fill body, options,
personas/from-validation, pricing/advisor), `app/services/business/service.py` (canvas `id`), `app/worker/handlers/ai.py`
(section fill + 3 handlers), `e2e/test_business_builder.py` (slice (b) journey).

## Verification
Fresh-DB pytest under UTC: 2345 passed, 97.40% coverage. black/isort/ruff/mypy/bandit clean, pylint 9.88, semgrep (CI
configs) clean. Alembic: single head; upgrade, `check`, and downgrade base → upgrade on a scratch DB. e2e 72/72. Real
model on the local stack: section AI, M&V options and the advisor all returned relevant output
(`e2e/_captures/business_prd_b_live/`).

## Operate / roll back
`alembic upgrade head` (0053, metadata-only), then **restart the worker** (new handlers). Roll back: revert, then
`alembic downgrade 0052_business_canvas_versions`.

## Follow-ups
- Slice (c): plan options, readiness, stage progress, section regenerate and failed state; revenue → Finance (D-BB-5).
- Personas-from-validation hasn't been run against the real model (no local interviews). Run it once Validation is walked.
