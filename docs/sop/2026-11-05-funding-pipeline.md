# Funding Hub: round and investor pipeline (slice A)

**What shipped:** the API behind the Funding Hub **Pipeline** page (PRD 14.1): one raise (round) per workspace, an investor pipeline on the PRD's seven fixed stages, stage history, an interaction log, the header-bar numbers, a `funding.round.committed_changed` notification, and a founder-confirmed hand-off to the Finance ledger. API only; FE guide `docs/fe-integration-guide-funding.md`. Branch `feature/funding-pipeline`, stacked on `feature/readiness-score` (API #216). PR: see the branch. Migration **`0090_funding_core`** (on `0089_readiness_scores`).

**Why:** Module 14 is six mock pages with no backend (no model, route, event or notification). The pipeline is the spine the other five pages build on (data room shares point at investors, term sheets link to them, analytics read `stage_entered_at` and the stage history). Design and the accepted calls (D-FUND-1..8, lead defaults L1..L10): `cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md` and `cofoundaz/docs/checklist/funding-prd-gaps.md`. Gap rows closed here: 1 to 7, 9, 10 (empty state only), 37, 38 (FE copy note).

**How**

| Topic | Decision |
|---|---|
| Rounds (L1) | `funding_rounds`; a partial unique index (`status <> 'closed'`) allows one unfinished round per workspace, closed rounds are history. `PUT /round` upserts the unfinished one or starts a new one. The first investor auto-creates a planning NGN round so "Add your first investor" needs no setup. After a close, `POST /investors` is `409 ROUND_CLOSED` until the founder starts the next round explicitly (never silent). |
| Money (D-FUND-7) | `BIGINT` minor units, one currency per round (`NGN` default, `USD`), no FX. Currency cannot change once an investor on the round has an amount (422). |
| Stages (L8) | Fixed seven (`research` ... `passed`) as a string column with a CHECK, not a PG enum. Passed requires one of five reasons; a DB check ties `passed_reason` to the Passed stage. Every move writes a `stage_change` event and resets `stage_entered_at`; same-stage + `position` reorders without history. Positions are re-numbered in both columns. |
| Header bar (L2) | `soft_minor` = Term Sheet investors with an amount, `committed_minor` = Committed investors with an amount, percentages uncapped and null without a target. |
| Suggested target (L1) | 18 months of Finance net burn (`cash_flow_summary.monthly_burn`), NGN rounds only, a hint never auto-applied. |
| Finance hand-off (L3) | Moving to Committed with an amount returns an **offer** only. `POST /investors/{id}/record-funding` (founder confirms the money arrived) writes one `Funding` inflow through `finance.service.create_transaction` and the same runway re-evaluation Finance's own route runs. `recorded_transaction_id` makes it once-only (409). A USD round needs a naira `amount_minor` because Finance is NGN only. |
| Roles (D-FUND-5) | `FUNDING_ROLES` = founder + team member via `require_role`; everyone else 403, other workspace's ids 404. Plan gate: `assert_feature("funding")` after the role check (L10), dark until `PLAN_ENFORCEMENT`. |
| Notification (L6) | `funding.round.committed_changed`, fired only when the Committed total changes. Audience `FUNDING_ROLES` minus the actor; category Finance via the `funding.` prefix (no new Preferences row until the Funding FE exists). |
| Concurrency (review fixes) | `record-funding` and `move_stage` take a row lock on the investor (`get_investor_locked`). A concurrent first-investor create or `PUT /round` that trips `uq_funding_round_one_active` is caught in a savepoint and re-read (joins the winner's round) instead of a 500. |
| Frozen once recorded | After `recorded_transaction_id` is set, changing that investor's `committed_minor` is `409 INVESTOR_RECORDED` and changing the round currency is `409 ROUND_HAS_RECORDED_MONEY`, so the ledger and pipeline can't disagree. `investors.recorded_transaction_id` is indexed (edited into `0090` in place, unmerged). |
| Not logged to activity | Investor names are deliberately kept out of the workspace activity feed (the open activity-feed role leak). |

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/funding.py` (`FundingRound`, `Investor`, `InvestorEvent`), enums in `app/db/models/enums.py` (`FUNDING_ROLES`, stage/reason/kind enums), `alembic/versions/0090_funding_core.py` |
| Schemas | `app/schemas/funding.py` |
| Services | `app/services/funding/{rounds,investors,overview,errors}.py` |
| API | `app/api/v1/endpoints/funding.py`, wired in `app/api/v1/api.py`: `GET/PUT /funding/round`, `GET /funding/overview`, `GET/POST /funding/investors`, `GET/PATCH/DELETE /funding/investors/{id}`, `POST .../stage`, `GET/POST .../events`, `POST .../record-funding` |
| Notifications | `app/services/notifications/{registry,audience,categories}.py`, `tests/services/notifications/test_dashboard_audience.py` |

**Verification:** `tests/api/test_funding_pipeline_api.py` (7 roles on every route, refused roles on an existing investor, cross-workspace 404/403, plan gate 402, round lifecycle, validation, stage rules, reorder, filters, totals, overdue, suggested target, notification recipients, Finance hand-off incl. USD and the ledger cap); `tests/test_funding_migration.py` (single head, one-unfinished-round index, investor CHECKs, cascade); live journey `e2e/test_funding_pipeline.py` against a real server (38 captures in `e2e/_captures/funding_pipeline/`, source of the FE guide). Local: black, isort, ruff, mypy; migration upgrade, downgrade, upgrade and `alembic check` clean on a scratch DB. CI runs the full suite.

**Operate / roll back:** no env var, no AI spend, no worker job. Revert by downgrading `0090` (drops the three tables; Finance rows already written by `record-funding` stay as ordinary ledger entries) and reverting the PR. The page stays hidden in the sidebar until slices A to F are merged and re-walked.

**Follow-ups**

- A real two-session concurrency test for the record lock and the round race (two DB connections, one blocked on the other's `FOR UPDATE`); today the lock is asserted by a spy and the race by simulating a stale read.
- **Finance ledger cap:** `transactions.amount_minor` is a 32-bit integer, so one Finance entry is at most ₦21,474,836.47. A ₦90M raise cannot be recorded in one entry; `record-funding` refuses clearly (`fits_ledger: false`) and allows one smaller part. The proper fix is a Finance change (BIGINT + schema limit); Adebayo's call.
- Investor directory (D-FUND-1 b) follows once Adebayo approves the list; `investors.source` and `directory_id` are ready for it.
- "Prep me for this meeting", calendar link on the Meeting stage, readiness banner and analytics are slice F.
- Cap-table SAFE draft on commit (D-FUND-L3, second half) arrives with slice C.
- FE: the footer tip copy (row 38) and the board, drawer, Add investor and Passed modal wiring.
