# SOP: Assessment PRD (Module 07) backend gaps

| | |
|---|---|
| **What shipped** | Gap rows 5, 6, 7, 8, 9, 11, 12, 13 (BE parts) of the Assessment gap list (`cofoundaz/docs/checklist/assessment-prd-gaps.md`), decisions D-ASM-1..4. Branch `feat/assessment-prd` (PR number filled in when opened). |
| **Why** | Security: any workspace member (mentor, investor, consultant) could read a founder's assessment answers and narrative, and was notified about completions and re-assessments. Product: the FE had no progress, no status filter, no Health Score change after completing, no prompts/labels/changes in the detail view, no stage-at-the-time, and no way to know a re-assessment was due. |

## How (decisions, all reversible)
| Decision | Why | Rejected |
|---|---|---|
| D-ASM-1: reads (`GET /assessments`, `/compare`, `/{id}`) use `require_role(founder, team_member)`; others get the standard 403 `FORBIDDEN` | answers + narrative are founder business data; same dependency and envelope as the write routes | a new permission system; hiding fields per role |
| Notification audiences: `assessment.completed` to founders + team members minus the completer; `assessment.quarterly.due` to founders only. The completer is excluded by adding `actor_id` to the `assessment.completed` event payload (it was never in the payload, so the old "minus actor" rule did nothing) | a mentor must not learn that an assessment happened or is due | filtering at read time |
| D-ASM-2: `progress = {answered, remaining, pct}`; `pct = answered*100 // (answered+remaining)`, counting only questions applicable given the current answers (`engine.progress`). It can move when an adaptive answer opens a follow-up. Added next to `next_question` on start, GET next-question, POST answers; on list rows only while `in_progress` (`null` otherwise) | uses the engine's own applicability, so it can never disagree with what the flow will ask | a fixed denominator (would be wrong the moment a follow-up appears) |
| `GET /assessments?status=in_progress\|completed`, `Literal` query type, other values 422 `VALIDATION_ERROR` | existing envelope for bad input | silently ignoring |
| D-ASM-4: `/complete` adds `health: {score, previous_score, delta, dimensions:[{key,score,previous,delta}]}`. The previous `HealthScore` row is read just before `recompute_health_score`; `null` previous when none. A replayed `/complete` returns `health: null` | the before-picture only exists for the call that recomputes; storing it would need a new table | persisting snapshots; deriving from `health_score_history` (not tied to an assessment id) |
| Row 11: additive `answers` list on detail: per dimension, per question `{question_key, prompt, qtype, value, answer_label, changed, changed_from}`. Previous = latest COMPLETED assessment of the same startup finished before this one (latest completed for an in-progress one). Multi-choice compares as sets. `answers_by_dimension` untouched | additive, FE can migrate at its own pace | replacing `answers_by_dimension` |
| D-ASM-3: `assessments.stage_at_time` (nullable `varchar(20)`), written in the same atomic UPDATE that claims completion; old rows stay `null` | "not recorded" is honest; a back-fill would invent history | back-filling from the startup's current stage |
| Row 13: `meta.quarterly_due` on the list (the list stays a bare array; `AssessmentListMeta(Meta)` adds the flag). Rule: latest completed >= `QUARTERLY_REASSESS_DAYS` (90) old and nothing in progress, the same predicate as `scheduler._due_quarterly` (test asserts agreement). The cron's per-quarter de-dup is deliberately not part of the flag | banner must stay on until the founder acts, the cron only de-dups notifications | changing `data` to an object (breaking) |
| Quarterly notification: body "Start re-assessment", payload gains `href: "/assessment"` (handler `handle_assessment_quarterly`) | CTA copy + deep link from the gap list | |

## What's involved
- Migration `0060_assessment_stage_at_time` (adds one nullable column; downgrade drops it). **`down_revision = 0058_mvp_feedback`: revision 0059 is reserved for the Legal work, which is not merged. Whoever merges second must re-point `down_revision` to the other branch's head** (CI enforces a single head: `alembic heads`).
- `app/db/models/assessment.py` (`stage_at_time`), `app/services/assessment/engine.py` (`progress`), `app/services/assessment/service.py` (`progress_for`, `_health_change`, `complete_assessment(actor_id=)`, `quarterly_due`, `answer_label`, `previous_completed`, `answer_groups`), `app/api/v1/endpoints/assessments.py` (role gate, filter, progress, meta, detail), `app/services/notifications/registry.py` (audiences, CTA), `app/worker/handlers/scheduled.py` (`href`).
- Tests: `tests/api/assessment/test_prd_{access,list,complete_health,detail_answers}.py` (+ `helpers.py`), `tests/services/assessment/test_progress.py`, `tests/services/notifications/test_assessment_audience.py`, `tests/test_assessment_stage_migration.py`; two existing tests relaxed to ignore `health` on replay (`test_complete.py::test_complete_is_idempotent_no_reenqueue`, `test_complete_concurrency.py`) and `tests/worker/test_scheduled_handlers.py` expects `href`.
- e2e `e2e/test_assessment_prd.py`, captures `e2e/_captures/assessment_prd/`, guide `docs/fe-integration-guide-assessment-prd.md`.

## Verification
See the hand-back numbers in the PR description; summary: TDD (tests written first, red on import/assert before the code), full unit suite green with CI's two-database layout and coverage gate, black / isort / ruff / mypy clean, `alembic upgrade head`, `alembic check`, `downgrade base`, `upgrade head` clean, e2e 77/77 including the new journey (mentor 403, status filter, progress, health deltas, `changed_from`, backdated 90-day clock + real scheduler tick + worker drain for the quarterly notification).

## Operate / roll back
Deploy: `alembic upgrade head` (CD runs it). No worker restart is needed for behaviour (the handler change only adds a payload key), but restart anyway if the worker image is rebuilt. Roll back: revert, then `alembic downgrade -1` (drops `stage_at_time`; the snapshot is lost, nothing else depends on it). The FE must be rolled back first if it reads the new fields; it must also be able to cope with `403` on the three reads (role gate) before this deploys.

## Follow-ups
- Other notification types still fan out to every member, mentors included (`healthscore.dropped`, `healthscore.record`, `mission.ready`, `roadmap.milestone.*`, `finance.runway.low`, `business.plan.generated`, ...). Same class of leak as row 6, not in this scope; needs its own audience review.
- `assessment.completed` notification `data` carries `dimension_scores`; fine for founder/team member recipients but worth trimming if the audience ever widens.
- Replayed `/complete` returns `health: null`; if the FE needs the numbers on a reload, read `GET /health-score` (or we persist the before/after pair).
- Re-point migration 0060 if 0059 (Legal) merges first.
