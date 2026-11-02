# SOP: Validation Hub PRD slice (a): overview, Design experiment, hosted smoke-test pages, deletes

| | |
|---|---|
| **What shipped** | Gap rows 2-6 and 14 of `cofoundaz/docs/checklist/validation-prd-gaps.md`. Branch `feat/validation-prd-a`. |
| **Why** | The Validation overview stats and nudge were mock, "Design experiment" only toasted, smoke tests had no page (visits and signups were typed by hand), the verdict lived in the FE, there was no milestone notification and nothing could be deleted. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| D-VAL-1 (confirmed): pages at `/p/{slug}` on the app domain; API under `/validation/pages/{slug}` | subdomains are parked | `{slug}.cofoundaz.site` (needs wildcard DNS/TLS) |
| Slug = unique column copied from `config.slug`; 3-60 `[a-z0-9-]`, reserved words | it's a URL; keeps the builder's existing payload | a separate publish endpoint |
| D-VAL-6: every claimed slug stays reserved to its workspace (`smoke_test_slugs`) | security review: a deleted/renamed test must not let another workspace take over the URL and collect emails from old links | releasing slugs on delete |
| Visitors counted by a SHA-256 of a browser-generated id (required, 8-64 chars); rate limits 60/min (page, events), 20/min (signups) | count people not reloads; no IP or cookie stored | server-signed visitor tokens (anyone can reload a page to mint one anyway) |
| Only PII stored: the email a visitor types; repeats are silent and stored once (partial unique index) | the page must not reveal who already signed up | |
| D-VAL-4: verdict server-side, only once `visits >= 100` | PRD "verdict when sample ≥ threshold" | FE-side verdict |
| D-VAL-5: milestones at 10/25/50/100/250/500/1000 signups → "{test} just passed {n} signups" to founders + team members | PRD copy, sensible thresholds | every N signups |
| "Design experiment" = AI job; the draft lives in `job.result`, nothing saved | PRD 09.6 nudge; founder edits before saving | auto-creating a draft experiment |
| Hand-entered `metrics.visits/signups` still add to the counts | tests hosted elsewhere keep working | dropping manual numbers |
| Deleting an assumption unlinks it from interviews and experiments; deleting an experiment deletes its events (incl. emails) | no dangling ids; data minimisation | soft delete |

## What's involved
- Migration `0056_smoke_test_pages`: `experiments.slug` (unique) + `published_at`, `smoke_test_events`, `smoke_test_slugs`.
- `app/services/validation/smoke.py` (new): slug rules + reservation, public page, events, signups + milestones, stats/verdict, waitlist.
- `app/services/validation/service.py`: overview, deletes, slug applied on create/update, stats delegate.
- `app/api/v1/endpoints/validation.py`: `GET /overview`, `POST /assumptions/{id}/experiment-draft`, `GET /pages/{slug}`,
  `POST /pages/{slug}/events|signups`, `GET /smoke-tests/{id}/signups`, `DELETE` ×4; slug race → 409.
- `app/worker/handlers/validation.py` (new, registered in `app/worker/__main__.py`): `validation.experiment.draft`.
- `app/services/notifications/registry.py`: `validation.smoke_test.milestone`.
- Tests `tests/api/test_validation_prd_a.py` (20); e2e `e2e/test_validation_prd_a.py`; guide `docs/fe-integration-guide-validation-prd-a.md`.

## Verification
Unit 2417 passed @97.45%; black, isort, ruff, mypy, pylint 9.88, bandit clean; alembic upgrade/check/downgrade base/upgrade
clean; e2e 74/74 including the new journey (captures in `e2e/_captures/validation_prd_a/`). Real model: "Design
experiment" on the local stack produced a sensible draft (preorder_click, 5% bar, no dashes).

## Operate / roll back
`alembic upgrade head` (CD runs it) and restart the worker (new job handler). Roll back: downgrade to
`0055_mission_roadmap_backfill` (drops slugs, events and reservations: waitlist emails are lost) and revert.

## Follow-ups
- FE: `/p/[slug]` public route, builder publish + slug errors, server verdict, waitlist view/CSV, overview + nudge +
  Design experiment, delete buttons, notification deep link (sub-session).
- Slices (b) interview scripts + Insight Synthesizer and (c) MVP feedback.
- Move to `{slug}.cofoundaz.site` if subdomains are un-parked (old links must redirect).
