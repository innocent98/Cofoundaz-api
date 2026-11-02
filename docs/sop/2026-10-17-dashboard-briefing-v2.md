# SOP — Dashboard briefing v2: Risks/Opportunities rows, "Do it", mission empty reason (backend)

**What shipped (one line):** The daily AI briefing now returns structured risk/opportunity rows and 1 to
3 proposed actions. A new **Do it** endpoint adds an action to today's mission. The Mission widget
learns *why* it's empty. A "briefing ready" notification fires. A pre-existing bug that meant **no
worker-published event ever became a notification** is fixed. Branch `feat/dashboard-briefing-v2`
(stacked on `feat/dashboard-kpis` / #129) → PR to `develop`.

## Why
A live walkthrough of the dashboard against PRD 02.1 (gap list:
`cofoundaz/docs/checklist/dashboard-prd-gaps.md`, rows 3, 4, 5, 9 and 19) found:
- Risks and Opportunities showed their **empty** copy even when the AI had written them: the BE sent one
  paragraph, but the PRD (and the FE) want rows with a severity dot and a **Review** CTA.
- The briefing had no **Do it** (it was removed because it faked completion). The PRD specifies
  `POST /dashboard/briefing/{id}/actions/{n}/accept`.
- On a weekends-off Saturday the Mission widget said "Complete your kickoff assessment", which is wrong.
- "Your daily AI briefing is ready" (PRD notification) didn't exist.

## How (key decisions)
| Decision | Why | Rejected |
|---|---|---|
| Strict JSON schema with `risks[]`/`opportunities[]`/`actions[]`, module from a **closed enum**, server maps module → `href` | The model can never author a URL or link to a non-route | Letting the model return hrefs |
| Server-side `normalize_*`: clamp to 3, drop blank rows, coerce bad severity → `medium`, unknown module → `null`, navigate-without-module → task | Real models drift from `maxItems`/enums | Trusting structured output blindly |
| **Do it → today's mission** (user decision D-DASH-1). `navigate` actions only record the click | Lands the AI's advice in the daily loop. Nothing happens without a click | Always deep-link; roadmap backlog |
| Accept is **idempotent** and row-locked (`SELECT … FOR UPDATE`), storing `accepted_task_id` on the action | A double-click must not add two tasks | A unique index on a task column |
| `ensure_today_mission` (race-safe SAVEPOINT, starts a user mission without a roadmap), now also used by `POST /missions/tasks` | One correct path. The endpoint's old inline version could race on `uq_mission_startup_date` | Duplicating the logic |
| 409 `BRIEFING_STALE` for an earlier day's briefing | A tab left open overnight shouldn't add yesterday's advice to today | Silently accepting |
| Briefing context includes real runway/revenue (via `kpi_metrics`) → so the briefing/risks/opportunities are **restricted** to founder/team_member | Grounds "watching runway" in numbers without leaking finance through AI prose to investors/mentors/advisors/accountants (same gate as the KPI tiles) | Showing everyone the text |
| `mission.empty_reason` = `weekend_off` / `all_caught_up` (null with tasks; `mission: null` = no roadmap) | The FE can say the PRD copy "Weekends off. Rest is a strategy too." | FE guessing from the date |
| Worker registers the notification subscribers | It never did, so business-plan-ready, mission-ready, milestone-overdue, quarterly check-in and now briefing-ready were all silently dropped | — |
| Legacy prose columns still written (rows joined) | Older readers keep working | Dropping them now |

## What's involved
- Migration `alembic/versions/0048_briefing_structured.py`: `daily_briefings.{risk_items,opportunity_items,actions}` JSONB NOT NULL default `'[]'` (chains off `0047_ai_chat`).
- `app/db/models/dashboard.py` (new columns) · `app/services/dashboard/ai_briefing.py` (schema + prompt) ·
  **new** `app/services/dashboard/briefing.py` (`normalize_*`, `serialize_blocks`, `accept_action`, `MODULE_HREFS`) ·
  `app/services/dashboard/service.py` (`_briefing_blocks`, `BRIEFING_ROLES` gate, `empty_reason`, `_finance_context`) ·
  `app/services/mission/service.py` (`ensure_today_mission`, `mission_empty_reason`) · `app/api/v1/endpoints/mission.py` (uses it) ·
  `app/api/v1/endpoints/dashboard.py` (`POST /dashboard/briefing/{id}/actions/{index}/accept`) ·
  `app/worker/handlers/ai.py` (normalize + publish `dashboard.briefing.ready`) · `app/services/notifications/registry.py` (spec) ·
  `app/worker/__main__.py` (register notifications).
- Tests: `tests/services/dashboard/test_briefing_actions.py` (new), updated `test_ai_briefing.py`, `test_briefing_generation.py`,
  `tests/worker/test_dashboard_briefing_handler.py`, `tests/worker/test_entrypoint.py` (a regression test that fails without the fix).
- e2e: `e2e/test_dashboard_ai_briefing.py` (+accept, idempotent, 404), `e2e/test_dashboard_kpis.py` (+investor restricted/403).
- Docs: `docs/fe-integration-guide-dashboard-briefing-v2.md`.

## Verification
- pytest **2210 passed, 97.47%**. black/isort/ruff/mypy/bandit clean, pylint 9.88. `alembic check` reports no drift, single head `0048`.
- `scripts/e2e_run.sh` **67 passed**.
- Live on local docker with the **real LLM**: structured rows + server hrefs; Do it (task) added to today's mission; repeat returned the same task; navigate returned `/roadmap`; out of range 404; `empty_reason` `weekend_off` → `null`; after the worker fix the "Your daily AI briefing is ready" notification arrived. Captures are in `e2e/_captures/dashboard_briefing_live/`.

## Operate / roll back
Run `alembic upgrade head` (additive columns with defaults; fast). **Restart the worker** after deploy
(handler + registration change). Roll back: revert the PR, then `alembic downgrade 0047_ai_chat` (drops the
three columns; v2 rows are lost, prose survives).

## Follow-ups
- The briefing-ready notification has no email category (in-app only). Decide whether it belongs under a digest category.
- 409 `BRIEFING_NOT_READY` / `BRIEFING_STALE` are unit-tested only.
- Realtime activity, search, invite and KPI snapshots follow as the next dashboard BE slices (gap doc build order).
