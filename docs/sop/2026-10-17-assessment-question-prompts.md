# SOP — Assessment: serialize question prompt text (unblock the FE)

**What shipped (one line):** Added a founder-facing `prompt` (and optional `helper`) to every
assessment question and serialized it on the question endpoints, so the Startup Assessment can
finally be wired on the FE. Branch `fix/assessment-question-prompts` → PR to `develop`.

## Why
The assessment question object (`app/services/assessment/bank.py::Question`) had **no
human-readable text** — only `key`, `dimension`, `section`, `qtype`, `scoring`, `options`. So
`serialize_question` could not return anything to display, and the FE assessment stayed on mock
questions (memory `assessment-missing-question-prompts`). This was the one backend gap blocking
the whole assessment flow — which the dashboard, Health Score, and Today's Mission all funnel
users toward.

## How
- Added `prompt: str = ""` and `helper: str | None = None` to the frozen `Question` dataclass.
  `prompt` defaults to `""` so synthetic engine-logic **test fixtures** (which build `Question`s
  inline to exercise `show_if`) don't need it; every real bank question sets it.
- Wrote prompt text (and scale legends as `helper`) for all **11** v1 questions.
- `serialize_question` now returns `prompt` + `helper` alongside the existing fields.
- Guard: `tests/services/assessment/test_bank.py` asserts every bank question has non-empty,
  distinct prompt text — so a new question can't ship promptless (stronger than a type requirement).

Decision: default-`""` + a bank guard test, rather than making `prompt` a required positional —
keeps the ~10 synthetic `Question(...)` fixtures in `test_engine.py` / `test_scoring.py` untouched
while still enforcing the real contract where it matters (the bank).

## What's involved
- `app/services/assessment/bank.py` — `prompt`/`helper` fields + text for all 11 questions.
- `app/services/assessment/service.py` — `serialize_question` returns `prompt` + `helper`.
- `tests/services/assessment/test_bank.py` — new guard test.
- `docs/fe-integration-guide-assessment.md` — new FE contract (live-captured).

## Verification
- **Live** (local full-stack): `POST /assessments` → first question now includes
  `"prompt": "How far along is your product?"`. Drove the **full adaptive flow** (11 questions,
  `show_if` branches for `product_confidence`/`mrr`/`ip_assigned`) → `POST /complete` → `200`
  with `dimension_scores` {product 89, market 88, money 86, legal 100, team 88}, overall 90, and
  a narrative. Captures are in `docs/fe-integration-guide-assessment.md`.
- **Gates:** ruff ✓, black ✓, isort ✓, mypy ✓ (changed files); full `pytest` run before push.

## Follow-ups
- **FE:** wire `cofoundaz/hooks/useAssessmentApi.ts` (currently mock) + the assessment pages to
  these endpoints (separate FE slice).
- The AI narrative upgrade (`ai.assessment.narrative`) already exists; the FE should re-read
  `GET /assessments/{id}` to pick up the richer narrative after completion.
