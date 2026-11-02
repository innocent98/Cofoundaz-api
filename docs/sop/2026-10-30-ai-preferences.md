# SOP: AI preferences (Settings S8)

**What shipped:** `GET/PATCH /ai/settings` with three per-member, per-workspace settings (proactive suggestions, daily briefing, tone) that change
behaviour, not just storage. Migration `0082_ai_preferences`. Branch `feature/ai-preferences`. Closes gap S8 in
`cofoundaz/docs/checklist/settings-prd-gaps.md` (the "What I remember" list, Forget and Clear all are deferred: no memory store). FE guide:
`docs/fe-integration-guide-ai-settings.md` (live-captured).

## Why
PRD 03.5 lists the toggles and tone; `/ai/settings` had no route, so the FE page was mock.

## How: decisions
| Topic | Decision |
|---|---|
| Scope of a setting | Per member per workspace (`ai_preferences`, unique on user + startup), like notification preferences. Defaults apply with no row. |
| Briefing off | The briefing is generated once per workspace per day, so "off" is a *view and trigger* switch: that member's dashboard returns `status: "off"` blocks and never triggers generation (no LLM spend if nobody with it on opens the dashboard), and the `dashboard.briefing.ready` notification skips them. |
| Proactive suggestions: exactly which surfaces | (1) the briefing's AI-suggested next steps (`actions`) and the Opportunities block; (2) the `learning.course.recommended` notification. **Not** Business Builder suggestions (`business.suggestion.created`): those are written by a human consultant. Mission "reason" text explains a task and is not a nudge. Add a surface by wrapping its recipients in `registry._respecting_ai_prefs` or branching on `get_prefs` where it is rendered. |
| Tone | A fixed enum (`straight_shooter`, `encouraging`) mapped to one fixed sentence in `services/ai_preferences.py::tone_instruction`, appended to the system prompt of the chat reply and the dashboard briefing. No user text ever reaches the prompt; an unknown value falls back to the default. The briefing is shared, so it uses the earliest active founder's tone. No JSON schema changed, so the strict-schema test is unaffected. |
| Access | Founder and team member (`_chat_access`), the roles that have the AI chat. |
| Strictness | Unknown fields, empty body and `null` values are 422. |

## What's involved
`app/db/models/ai_preference.py`, `alembic/versions/0082_ai_preferences.py`, `app/services/ai_preferences.py`, `app/api/v1/endpoints/ai.py`, `app/services/dashboard/service.py`
(`_briefing_blocks`), `app/services/dashboard/ai_briefing.py`, `app/services/ai_cofounder/prompts.py`, `app/worker/handlers/ai.py`,
`app/services/notifications/registry.py`. Tests `tests/api/test_ai_settings.py`; journey `e2e/test_ai_settings.py`.

## Verification
17 new tests plus the dashboard, chat, briefing-handler and notification suites green locally; migration round trip clean on a scratch DB; live journey captured. The real model's tone is not verified (stub LLM).

## Operate / roll back
No env vars. `alembic downgrade 0081_notifications_gaps` drops the table; code then needs reverting (the dashboard imports the service).

## Follow-ups
Memory list / Forget / Clear all (needs a memory store). Per-surface switches for more AI-initiated nudges as they appear. Tone on other prompts (mission reasons, health recommendations) if wanted.
