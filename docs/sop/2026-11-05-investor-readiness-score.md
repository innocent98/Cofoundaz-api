# Investor Readiness: score and history (slice A)

**What shipped:** the scoring engine behind PRD 15.1 and 15.5: a 0 to 100 Investor Readiness score over six dimensions, ranked gap notes with fix links, a cache, history, founder-entered market size, and a share-safe snapshot for Funding. API only; FE guide `docs/fe-integration-guide-investor-readiness.md`. Branch `feature/readiness-score`, PR: see the branch. Migration **`0089_readiness_scores`** (on `0088_content_reports`; Legal PR #214 also targets 0089, so whichever merges second re-chains: change `down_revision` and the revision id in one file).

**Why:** Module 15 is five mock pages with no backend. The score is the spine the other four pages (deck review, Mock Q&A, narrative check, history log) feed. Design and the seven accepted product calls (D-IR-1..7, 2026-11-05): `cofoundaz/docs/superpowers/specs/2026-11-05-investor-readiness-design.md` and `cofoundaz/docs/checklist/investor-readiness-prd-gaps.md`.

**How**

| Topic | Decision |
|---|---|
| Engine (D-IR-1) | Deterministic. One declarative table of 23 signals in `app/services/readiness/signals.py` (key, dimension, stage targets, href, reader, gap-note template, weight). No LLM, no tokens. |
| Reuse | Health Score readers are imported (`health_score.live_signals.SIGNALS_BY_KEY[...].read`): members, activity, roadmap, mission, assumptions, interviews, transactions, runway. New readers read Sales, Invoices, Validation, Business Builder, Documents, Financial Model directly. |
| Weights (D-IR-2) | Traction 25, Financials 20, Product 15, Market 15, Team 15, Story 10 (`config.py`). A signal scores `min(value/target,1)*100` against its stage target; a dimension is the weighted mean of its applicable signals; a signal that does not apply yet (`None`) drops out instead of counting 0. Overall renormalises over scored dimensions. |
| Cap (D-IR-2) | Overall is held at 69 until three must-haves exist: a financial model, a pitch deck, one traction proof point. `raw_score` and `must_haves` are always returned so the FE can say why. |
| Gaps | Candidate per below-target signal, ranked by overall points gained, at most 2 per dimension, top 5. Notes are templates filled with the real numbers, never model prose. |
| Cache and triggers | `readiness_scores` (one row per workspace). Refreshed on read when older than 15 minutes, on events (Health's list plus canvas, deal won, invoice paid, financial model, assumption validated/invalidated, document created), and by the nightly Health pass (`handle_health_nightly` also refreshes readiness). History row only on change. |
| Notification | `readiness.improved`, founders only, at most once a day, on a band improvement or a rise of 5+ points. Category `health_assessment`, module `readiness`. |
| Event | `readiness.updated {startup_id, score, previous_score, band, ready, computed_at}` on a persisted change. Funding reads it for the pipeline banner. |
| Roles (D-IR-4) | `READINESS_READ_ROLES` founder + team member; `READINESS_WRITE_ROLES` founder (`app/db/models/enums.py`). Everyone else 403. Funding's data-room share (not built) will use `GET /readiness/snapshot` for investors. |
| Market size | `readiness_inputs`; only counts with a source URL (weight 2 of 6 in Market, so at most a third). |

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/readiness.py` (`ReadinessScore`, `ReadinessScoreHistory`, `ReadinessInput`), `alembic/versions/0089_readiness_scores.py` |
| Service | `app/services/readiness/{config,scoring,signals,service,triggers}.py` (scoring is pure maths) |
| API | `app/api/v1/endpoints/readiness.py`, `app/schemas/readiness.py`, wired in `app/api/v1/api.py`: `GET /readiness/score`, `POST /readiness/score/recompute`, `GET /readiness/history`, `GET /readiness/snapshot`, `GET` and `PUT /readiness/inputs` |
| Touched elsewhere | notification registry, categories, audience (`readiness.improved`), `app/worker/handlers/scheduled.py` (nightly), `tests/services/notifications/test_dashboard_audience.py` |

**Verification:** unit tests `tests/services/readiness/` (scoring maths, every rule, events, throttle, notification limits, history markers) and `tests/api/test_investor_readiness_api.py` (all 7 roles on every route, validation); `tests/test_readiness_migration.py`; live journey `e2e/test_investor_readiness.py` against a real server (captures in `e2e/_captures/investor_readiness/`); migration upgrade, downgrade, upgrade and `alembic check` clean on a scratch DB. Coverage of the new service code 98%. Touched shared notification tests all pass.

**Operate / roll back:** no config, no env var, no AI spend. To revert, downgrade `0089` (drops the three tables) and revert the PR; the nightly handler change is two lines. A weights change is an edit to `config.py`: bump `READINESS_CONFIG_VERSION` (stored on every row and history point).

**Follow-ups**

- Slices B (deck review; tightens the "pitch deck" must-have to "a reviewed deck"), D (Mock Q&A), C (narrative check) add their Story signals and the History session log.
- Founder-agreement signal is a proxy (a founder agreement document plus any completed signature) because signature requests are not linked to rich documents.
- Revenue is scored in naira only; other currencies drop the signal until conversion exists. No month-on-month growth signal yet.
- "Learn how" Academy link per gap: the recommender is course-ranked, not tag-keyed, so it is not attached yet.
- Finance, validation and sales have no refresh events of their own beyond those listed; they rely on read-time staleness and the nightly pass.
