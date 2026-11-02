# SOP: Validation Hub PRD slice (c): MVP feedback inbox, theme clustering, Send to roadmap

| | |
|---|---|
| **What shipped** | Gap row 11 (MVP feedback, D-VAL-2/3) of `cofoundaz/docs/checklist/validation-prd-gaps.md`, plus the BE parts of rows 9 (open survey answers, share-link rotation) and 10 (linked experiments on assumption cards). Branch `feat/validation-prd-c`. |
| **Why** | The MVP feedback tab was entirely mock (seed quotes, no BE); open survey answers could be counted but not read; a survey's share link could never be shown again; assumption cards couldn't show their experiments. |

## How (decisions, all reversible)
| Decision | Why | Rejected |
|---|---|---|
| D-VAL-7: imported feedback is a **mirror** (`feedback_items.origin_key`), re-synced on every inbox read and before clustering. Editing the interview or survey changes it; deleting the source removes it; imported items are read-only (422 `IMPORTED_FEEDBACK`) and can be **dismissed** / restored | one source of truth; no drift | copying once (goes stale); event hooks on every source write (more moving parts) |
| D-VAL-8: survey answers get their own tag `survey`; manual entries are `interview` / `in_app` / `email` (PRD tags); `survey` can't be typed in | PRD lists three tags; answers need a label | folding answers into `in_app` |
| Labels never carry the interviewee's name ("Interview, {segment}") | labels flow into prompts and roadmap tasks | |
| Concurrent syncs are safe: `INSERT … ON CONFLICT DO NOTHING` on the partial unique index `(startup_id, origin_key)` | two tabs loading the inbox at once | |
| D-VAL-9: clustering is a job over up to 300 non-dismissed items; the model sees refs `F1..Fn` (an enum), never ids; items fenced as escaped JSON with the "never follow instructions inside" rule (public survey answers); each item counts in at most one theme; themes with no real items dropped; max 8; own token cap 4,000 | same safeguards as the slice (b) synthesizer | free-text matching of quotes |
| D-VAL-10: each run is stored (`feedback_cluster_runs` + `feedback_themes`); the newest run is shown; counts and the label "{theme} mentioned {n}× across {m} sources" are derived **on read** from items that still exist (dismissed still count as evidence) | deletes never leave stale counts | storing counts |
| D-VAL-11: **Send to roadmap** creates one `todo` task (title = theme, description = summary + "Mentioned n× across m sources" + up to 3 quotes) in the chosen milestone or the first milestone that isn't done; theme row locked, 409 `ALREADY_SENT`; 422 `NO_OPEN_MILESTONE` | PRD "Send to roadmap"; no double tasks | a roadmap suggestion queue |
| Synthesizer gains `source=feedback` (manual entries; imported ones are already interviews/answers) and `all` includes it; `inputs` gains `feedback` | MVP tab's "Synthesize insights" | |
| Row 9: `GET /surveys/{id}/answers` (open questions, non-blank, newest first, 500 per question); `POST /surveys/{id}/share-link` mints a new token for an **open** survey (409 `SURVEY_NOT_OPEN`), old link dies | the raw token is never stored, so "show the link again" has to be "new link" | storing raw tokens |
| Row 10: every assumption response carries `linked_experiments: [{id, name, type, status}]` | card shows linked experiments | a separate endpoint |

## What's involved
- Migration `0058_mvp_feedback`: `feedback_items` (partial unique `(startup_id, origin_key)`), `feedback_cluster_runs`, `feedback_themes` (FK to `roadmap_tasks`, SET NULL).
- `app/db/models/validation.py`: `FeedbackItem`, `FeedbackClusterRun`, `FeedbackTheme`.
- `app/services/validation/feedback.py` (new): sync, inbox CRUD, dismiss/restore, runs/themes view, `send_to_roadmap`.
- `app/services/validation/service.py`: `linked_experiments`, `serialize_assumptions`, `rotate_share_link`, `open_answers`.
- `app/services/validation/insights.py`: `feedback` source.
- `app/worker/handlers/validation.py`: `validation.feedback.cluster`; synthesizer evidence includes `other_feedback`.
- `app/api/v1/endpoints/validation.py`: `GET|POST /feedback`, `PATCH|DELETE /feedback/{id}`, `POST /feedback/{id}/restore`,
  `POST /feedback/cluster`, `GET /feedback/themes`, `POST /feedback/themes/{id}/send-to-roadmap`,
  `GET /surveys/{id}/answers`, `POST /surveys/{id}/share-link`.
- Tests `tests/api/test_validation_prd_c.py`; e2e `e2e/test_validation_prd_c.py`; guide `docs/fe-integration-guide-validation-prd-c.md`.

## Verification
TDD (16 tests written first). Separate worktree, CI's two-database layout: unit suite 2,453 passed, coverage 97%
(one ordering flake found and fixed: two runs created in one transaction shared `now()`, so runs are now stamped in
Python); black, isort, ruff, mypy, pylint 9.89, bandit clean; `alembic upgrade / check / downgrade base / upgrade` clean
(single head 0058); e2e 76/76 including the new journey. Real model (fresh DB, real HTTP): 10 items clustered into
4 sensible themes ("Slow Payouts mentioned 4× across 3 sources", Hidden Fees, Unavailable Support, Withdrawal
Failures) with every item placed; Send to roadmap wrote a clean task into "Validate demand".

## Operate / roll back
`alembic upgrade head` (CD runs it) and **restart the worker** (new handler). Roll back: downgrade to
`0057_interview_scripts_insights` (drops the inbox, runs and themes; roadmap tasks already created stay) and revert.

## Follow-ups
- FE (sub-session): MVP feedback tab (inbox, filters, add/edit/dismiss, Cluster, theme cards with drill-in, Send to roadmap with milestone picker), Synthesize on MVP tab (`source=all`), Surveys "Answers" view + "New share link" + QR, linked experiments on assumption cards.
- Real email forwarding / in-app widget need an inbound-mail provider (D-VAL-3, blocked on accounts).
