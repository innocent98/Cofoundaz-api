# SOP — Business Builder backend, PRD completion slice (a): last edited, versions, export, suggest mode

**What shipped (one line):** The backend for the Business Builder editor header and access gaps: who/when last edited,
per-canvas version history with restore, Export to a Document, consultant-only suggest mode with the PRD founder
notification, and overview metadata (last edited + a Business Plan row). Branch `feat/business-builder-prd-a` → PR.

## Why
Live walkthrough 2026-10-19 (`cofoundaz/docs/checklist/business-builder-prd-gaps.md`, 21 rows). The PRD's shared editor
header (last edited, version link, Export) had no backend at all. Export on the BMC editor was a fake toast. Any role
could POST a suggestion, and every member got a generic "A consultant suggested a change". The overview had no plan
tile or last-edited data. Persona `fields` leaked Python reprs (`"<class 'str'>"`).

## How (decisions, D-BB-1..3 confirmed by Adebayo 2026-10-19)
| Decision | Why | Rejected |
|---|---|---|
| D-BB-1: snapshot the canvas **after every write** (`business_canvas_versions`), capped at 50 per canvas; restore writes a **new** version via `save_canvas` (same 409 rule) | One write path (`save_canvas` / `snapshot_canvas`) covers PUT, AI fill, approved suggestions and restore; history is never rewritten | Diff-based history (more complex to restore); "last edited only" |
| Snapshot `source` = manual / ai / suggestion / restore; approved suggestions credit the **consultant** | "Who wrote this" is the author, not the approver | Crediting the founder |
| Records get `updated_by_id` only, no history | Records are small, and the PRD's version link is on artifact editors | Versioning every record |
| D-BB-2: Export creates a Module 18 Document (`kind=custom`, draft); one section per block / per record; empty → 422 `NOTHING_TO_EXPORT` | Reuses Documents' versioning, sharing and PDF; one path for every artifact | Client-side file, or server PDF per artifact |
| D-BB-3: `POST /suggestions` requires `business_consultant` | PRD 08 access line; mentors etc. stay read-only | Any non-editor role |
| Suggestion notification → **founders only**, title "{name} suggested changes to {artifact}", body = CTA "Review suggestions"; plan notification "Your AI-generated business plan is ready" / "Open it" | PRD 08 notifications copy; body-as-CTA matches PRD 05/06 rows | Notifying every member |

## What's involved
**New:** `alembic/versions/0052_business_canvas_versions.py` (table + `updated_by_id` on `business_canvases` and
`business_records`), `app/services/business/export.py`, `tests/api/test_business_prd_a.py` (17),
`docs/fe-integration-guide-business-builder-prd.md`, `e2e/_captures/business_prd/`.
**Changed:** `app/db/models/business.py` (`BusinessCanvasVersion`, `updated_by`), `app/services/business/service.py`
(`snapshot_canvas`, `save_canvas(user_id, source)`, versions/restore, `person`, overview), `records.py` (`updated_by`),
`suggestions.py` (author credit, notification payload), `record_defs.py` (`_type_name`), `app/worker/handlers/ai.py`
(AI fill snapshot), `app/api/v1/endpoints/business.py` (4 canvas routes + record export, `_suggester`),
`app/schemas/business.py` (`CanvasRestore`), `app/services/notifications/registry.py` (`_founders`, PRD copy).
Tests updated for the new contract: overview exact-set (+`plan`), suggestion tests now post as a consultant,
AI-fill snapshot assertion, and `tests/services/test_mission_generate.py::test_streak_broken_by_gap`, which failed every
Monday (the gap day was a weekends-off Saturday). It now pins "today" to a Thursday.
e2e: `e2e/test_business_builder.py::test_business_builder_prd_a_journey`.

## Verification
Fresh-DB pytest under UTC: 2331 passed, 97.57% coverage. black/isort/ruff/mypy/bandit clean, pylint 9.88. Alembic:
single head, `upgrade head`, `alembic check` (no drift), `downgrade base` → `upgrade head` on a scratch DB. e2e 71/71
via `scripts/e2e_run.sh`. The FE guide is pasted from those captures.

## Operate / roll back
`alembic upgrade head` (0052, metadata-only column adds + a new table) and **restart the worker** (AI-fill snapshots).
Roll back: revert, `alembic downgrade 0051_comments` (drops the history table and the two columns).

## Follow-ups
- Slices (b) and (c) per the gap list. FE: one sub-session for the shell, overview and editor header (this guide).
- Notification titles fall back to the consultant's email when they never set a name. Revisit if consultants get a
  name step at invite acceptance.
