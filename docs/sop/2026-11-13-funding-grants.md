# Funding Hub: grants (slice E)

**What shipped:** the API behind the Funding Hub **Grants** page (PRD 14.6): a human-verified catalogue of grants, credits and programmes; a weekly match job (plain filter, then the model scores the best ten); dismiss and "not relevant" that persist; **Start application** with starter questions; **Draft with AI** per question; an application tracker with statuses and deadlines; and grants added by hand. Founder and team member, API only. FE guide `docs/fe-integration-guide-funding-grants.md`. Branch `feature/funding-grants`. Migration **`0107_funding_grants`** (parent `0105_analytics_custom_metrics`; 0104 Marketplace D and 0106 Legal are reserved, so the lead re-chains at merge time). The module stays **hidden** in the sidebar (decision 2026-11-12).

**Why:** the mock had a fixed list of grants and no way to track an application (PRD 14.6). Design and calls: `docs/superpowers/specs/2026-11-05-funding-hub-design.md`; D-FUND-7 (match job, catalogue is curated by people, the model never invents a grant). The catalogue was drafted and checked by hand in PR #237 (`docs/funding/grants-catalogue-draft.csv`, approved by Adebayo 2026-11-13).

**How**

| Topic | Decision |
|---|---|
| Catalogue | `content/funding/grants-catalogue.csv` (the approved draft, 15 rows, 10 verified, 5 partial) is the source. `services/funding/grants_catalogue.py` validates the whole file (every problem listed at once), converts amounts to minor units, derives `country_codes`, `pan_africa` and `in_kind` from the stated text, and upserts by slug. Idempotent; a row that leaves the file is switched off (`active = false`), never deleted, because a workspace may already track it. Run as `python -m app.services.funding.grants_catalogue` in the deploy `migrate` step (`docker-compose.prod.yml`) and in `scripts/e2e_run.sh`. Every row keeps `verified_on` and `source_url`. Updating the catalogue is a PR editing the CSV. |
| Access | `FUNDING_ROLES` (founder, team member) on every route, then `assert_feature("funding")`. The job also re-checks the plan. |
| Matching | `grants.candidates`: drop retired rows and anything this workspace dismissed, marked not relevant, or is applying to; filter on country in code (a grant naming no known country is open to everyone; a founder with no country is not filtered; Africa-wide grants accept any African country); score 0 to 100 from country, industry words, stage words, verified vs partial and open vs closed. The best ten go to the model in one call, which returns a score and one sentence per grant. Catalogue and founder text are fenced and escaped with an instruction to treat them as data; output is validated (unknown slugs dropped, score clamped, why cut to 240 characters, dashes removed). **If the model fails on the last attempt, or the daily allowance is spent, the plain score and a templated reason stand**: matching never depends on the model (`ai_scored: false`). |
| Weekly run | `scheduler._due_grant_matches`: one `ai.funding.grant_match` job per workspace with the Funding plan per ISO week (claim key `funding.grant_match`/workspace/week). `POST /grants/matches/refresh` queues the same job on demand (deduplicated while one is queued or running). A run replaces the workspace's `suggested` rows (last week's that no longer rank are deleted) and never touches dismissed, not-relevant or applying ones. |
| Notification | `funding.grant.matched` ("N new grant matches for you, starting with X") to founders and team members, published once per run and only when something new appeared. |
| Application | `Start application` creates the tracker row (name, funder, amount and currency from the catalogue), four starter questions, and moves the match to `applying`; one application per catalogue grant per workspace (partial unique index); a second call returns the same row. Deleting it restores the suggestion and deletes the answers. Cap 200 applications, 20 answers each. |
| Tracker | Statuses `planning, drafting, submitted, awarded, rejected, withdrawn`; `submitted_at` is stamped once. `expired` is computed (deadline passed and still planning or drafting). Catalogue deadlines are free text and are never parsed: only the founder's own `deadline` is a date. `catalogue_stale` flags a source not re-checked for 180 days. |
| Draft with AI | Job `ai.funding.grant_draft`, per answer. 202 and `draft_status: queued`; the FE polls the application. An answer with text needs `overwrite: true`. The job never writes over text typed while it ran (the draft is dropped), re-raises provider errors until the last attempt and then marks `failed`, and a draft whose job died is reconciled to `failed` on read. The prompt tells the model to leave `[bracketed placeholders]` for missing facts rather than invent them. Spent allowance is 429 at queue time. |
| Rejected | Letting the model pick grants from memory (the catalogue is the only source); parsing free-text deadlines into dates; a per-grant model call without the plain fallback; auto-submitting anything. |

**Folded in from the #235 review (lows):** (a) failed extractions are capped (50 per workspace, 422 until some are deleted) so retries cannot grow the table without bound; (b) quotes shown for an extracted term are unescaped for `&lt;` and `&gt;` only (the prompt escapes angle brackets to keep the document inside its fence); (c) with no round yet there is no currency to disagree with, so a document in another currency is no mismatch (`round_currency` is `null`), instead of claiming the round is in the NGN default; (d) the reconcile of a dead extraction now writes a system audit row (no actor), through `audit_system`.

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/grant.py` (`Grant`, `GrantMatch`, `GrantApplication`, `GrantAnswer`), `app/db/models/__init__.py`, `alembic/versions/0107_funding_grants.py` |
| Catalogue | `content/funding/grants-catalogue.csv`, `app/services/funding/grants_catalogue.py` |
| Services | `app/services/funding/grants.py` |
| API | `app/api/v1/endpoints/funding_grants.py`, `app/schemas/grant.py`, `app/api/v1/api.py` |
| Worker | `app/worker/handlers/funding.py` (match and draft handlers), `app/worker/scheduler.py` (`_due_grant_matches`) |
| Notifications | `app/services/notifications/{registry,audience}.py` |
| Lows | `app/services/funding/term_sheets.py`, `app/services/funding/term_extract.py` |
| Tests | `tests/api/test_funding_grants_api.py` (32), `tests/services/funding/test_grants_catalogue.py`, `tests/test_funding_grants_migration.py`, additions in `tests/api/test_funding_term_sheets_api.py` and `tests/services/funding/test_term_extract.py`, helper `tests/grant_helpers.py`, journey `e2e/test_funding_grants.py` |

**Verification:** lint, format and mypy clean; touched test files green locally (grants API 32, catalogue 5, migration 5, term sheets 133 incl. the four lows, notification audience map); migration upgrade, downgrade, upgrade round trip on a scratch database; live e2e journey against a running server (canned model) whose responses are the captures behind the FE guide. The full suite runs on CI.

**Operate / roll back:** the deploy `migrate` step loads the catalogue after `alembic upgrade head`. To change the catalogue edit the CSV and deploy. Roll back with `alembic downgrade 0105_analytics_custom_metrics` (drops the four tables); the catalogue file can stay. Matching needs a worker running; with no model configured the plain scores are used.

**Follow-ups**

- A real model's scoring and drafting quality has not been exercised; the plain fallback and the placeholders are the controls.
- The sidebar entry stays hidden until Adebayo says otherwise; unhiding is an FE change only.
- Catalogue rows re-checked on a schedule: `stale` shows after 180 days, but nothing re-verifies them automatically.
- Currency conversion for totals (credits are in USD, other rows in NGN, EUR, GBP) is not done; the FE must not sum across currencies.
