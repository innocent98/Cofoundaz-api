# SOP: Validation Hub PRD slice (b): interview scripts + Insight Synthesizer

| | |
|---|---|
| **What shipped** | Gap rows 7, 12 and 13 of `cofoundaz/docs/checklist/validation-prd-gaps.md`. Branch `feat/validation-prd-b`, stacked on `feat/validation-prd-a` (API #149). |
| **Why** | "Generate script" only flipped a flag (hardcoded questions, seed assumptions); the Insight Synthesizer on Interviews/Surveys/MVP was a 2.5 s mock with a hardcoded report and an "Apply" that only toasted; both BE jobs were stubs with no handler; there was no `validation.insight.detected` event or notification. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| A generated script is **saved** (`interview_scripts`) and editable; `job.result.script_id` points at it | PRD 09.4: scripts appear in a list and are editable | draft-in-job-result like "Design experiment" (nothing to list) |
| 1 to 5 assumptions per script; 6-10 questions asked of the model, capped at 10 on save, 30 when edited | keeps prompts focused; founders may add their own | unlimited |
| Question ids are kept across edits; new questions get a 12-char hex id | stable references for the FE editor (same idea as survey question ids) | positional ids |
| The report is **stored** (`insight_reports`) with per-item ids; one report per run, history kept | Apply needs a stable target; the notification deep-links to it | report only in `job.result` |
| `source` = `interviews` / `surveys` / `all` (no body = `all`); 422 `NOTHING_TO_SYNTHESIZE` up front, `empty` job result if the data vanished while queued | matches the three tabs; no wasted model call | |
| The model sees assumptions as short refs `A1..An` (an enum in the JSON schema), never ids; a suggestion survives only if it maps to a real assumption **and** changes its status, one per assumption | the model can't target another workspace's id or a no-op | trusting returned UUIDs |
| Quotes are kept only if they appear (normalised) in the evidence it was given | the synthesizer must not put words in a customer's mouth | trusting model quotes |
| Inputs: interview segment, verdict, notes (1,500 chars), key quotes, linked assumption statements (30 newest interviews) + open survey answers (200 newest, 500 chars). **No interviewee names, no closed answers** | PII minimisation, same rule as personas-from-validation | |
| Apply locks the report row (`SELECT … FOR UPDATE`), calls `update_assumption` (validated/invalidated events fire as usual), stamps `applied_at`; second click 409 `ALREADY_APPLIED`; deleted assumption 404 | no double apply; one code path for status changes | PATCHing the assumption from the FE |
| `validation.insight.detected` once per report with the top theme, only when there is a pattern; copy "New pattern detected in customer feedback: {theme}", body/CTA "See insight"; in-app only | PRD copy | an email category (would add a new preference toggle) |
| Validation notifications go to **founders + team members** only (`_validation_members`); the slice (a) milestone notification was moved to it too | only those roles can open the Validation Hub; mentors etc. were getting test names and counts | `_all_active_members` |
| Over budget → `over_budget`, nothing stored | same as every AI job | |

## What's involved
- Migration `0057_interview_scripts_insights`: tables `interview_scripts`, `insight_reports` (startup FK cascade, index).
- `app/db/models/validation.py`: `InterviewScript`, `InsightReport` (registered in `app/db/models/__init__.py`).
- `app/services/validation/insights.py` (new): script CRUD + question cleaning, synthesizer input gathering, report CRUD, `apply_suggestion`.
- `app/services/validation/service.py`: `delete_assumption` also unlinks scripts.
- `app/worker/handlers/validation.py`: handlers `validation.scripts.generate` and `validation.synthesize` (prompts, schemas, cleaning).
- `app/api/v1/endpoints/validation.py`: `POST /scripts/generate` (now requires 1-5 assumptions), `GET /scripts`, `GET|PATCH|DELETE /scripts/{id}`,
  `POST /synthesize` (body `{source}` optional), `GET /insights`, `GET|DELETE /insights/{id}`, `POST /insights/{id}/suggestions/{sid}/apply`.
- `app/schemas/validation.py`: `ScriptUpdate`, `SynthesizeRequest`.
- `app/services/notifications/registry.py`: `_validation_members`, `validation.insight.detected`; milestone audience narrowed.
- Tests `tests/api/test_validation_prd_b.py` (20); `tests/api/test_validation.py` updated (the two stubs no longer accept empty calls).
  e2e `e2e/test_validation_prd_b.py`; guide `docs/fe-integration-guide-validation-prd-b.md`.

## Verification
TDD (tests written first). Unit suite, black, isort, ruff, mypy, pylint, bandit and `alembic upgrade/check/downgrade base/upgrade`
run in a separate worktree against scratch databases (numbers in the PR). e2e 75/75 including the new journey (captures in
`e2e/_captures/validation_prd_b/`). Real model (fresh DB, real HTTP): an 8-question Mom-Test script with tips; a report with 4
themes whose 9 quotes were all verbatim, a fair contradiction, and 2 cautious "untested → testing" suggestions; notification
"New pattern detected in customer feedback: Same-day cash matters".

## Operate / roll back
`alembic upgrade head` (CD runs it) and **restart the worker** (two new handlers). Roll back: downgrade to
`0056_smoke_test_pages` (drops scripts and reports) and revert. The FE must ship the 422s for empty
"Generate script"/"Synthesize" calls (the old stubs accepted them).

## Follow-ups
- FE (sub-session): script generator + list + editor, synthesizer report + Apply on Interviews/Surveys, notification deep link.
- Slice (c) MVP feedback adds feedback entries to `source=all` (and probably `source=feedback`).
- Gap row 9 (read open survey answers in the UI) still needs its own endpoint; the synthesizer reads them server-side only.
