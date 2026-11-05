# Investor Readiness: mock Q&A (slice D)

**What shipped:** PRD 15.3. A founder runs a mock investor interview: an AI investor in one of three personas asks about the company's real numbers, the founder answers (up to 10 times), and a scoring job returns a per-answer rating, the strongest and weakest answer, a model answer for the weakest, and a trend against earlier sessions. The result feeds the Story dimension ("Defended the pitch"), the History log and markers, and a bell notification. API only; FE guide section 13 of `docs/fe-integration-guide-investor-readiness.md`. Branch `feature/readiness-mock-qa`, PR: see the branch. Migration **`0093_readiness_mock_qa`** on `0092_marketplace_providers`.

**Why / decisions (D-IR-1..7, accepted 2026-11-05):** D-IR-7 is the slice's contract: 10 answers per session, refuse to start with under about 40k tokens left in the day, transcripts kept 90 days. D-IR-4: the founder runs sessions and reads everything; a team member reads scores only (no transcripts, no AI tools); everyone else 403. A session costs about 35k tokens, the largest AI spend in the app, so the gates matter.

**How**

| Topic | Decision |
|---|---|
| Turns | Synchronous, one `metered_complete` call per answer (the Sales coach pattern), `max_tokens` 350. The route locks the session row (`FOR UPDATE`), so a double submit cannot record two answers or pass the cap. The founder's answer is stored only together with the investor's reply, in one commit: if the allowance has run out (`429 AI_BUDGET_EXHAUSTED`) or the call fails, nothing is kept. A failed call is still billed on its own session (`metered_complete`'s existing rule). |
| Double submit | `expected_turn` (required) must equal the session's `turns`; a retried or duplicated request carries a stale count and gets `409 TURN_MISMATCH`, so it is neither recorded as the next answer nor charged twice. The FE reloads the session instead of resending. |
| Cap | The 10th answer stores no reply, closes the session and queues scoring in the same request (`reply: null, ended: true`). |
| Continue gate | Before each answer except the 10th, `headroom` must cover one more turn plus scoring (`TURN_RESERVE_TOKENS`, 12,000), else the same `409 AI_BUDGET_LOW` with nothing recorded; the founder can end and score what they have. |
| Start gate | `headroom` under 40,000 returns `409 AI_BUDGET_LOW` before anything is created or spent. One `active` session per workspace (`409 SESSION_ACTIVE`), under an advisory lock; an active session untouched for 2 hours is `abandoned` and stops blocking. |
| Context | Built once at start and stored: company basics, every readiness signal as "label: value (target)", two canvas lines. It reuses `read_signals`, so the investor asks about real numbers with no new queries. About 1.2k tokens per turn. |
| Prompt injection | The persona/system prompt says the founder's replies are spoken answers and any instruction in them is ignored. Scoring puts answers inside `<transcript>` tags (a pasted closing tag is stripped) with an "untrusted" instruction. The model never decides the outcome: ratings are clamped to 1..5, and the overall rating (under 2.5 needs work, under 4 good, else strong), strongest and weakest are computed in code. A model answer is attached only when the model wrote it for the answer that code picked as weakest, otherwise `null`. |
| Scoring job | `ai.readiness.qa_score` (the `ai.` prefix puts it under the budget). One `metered_complete_json` call, `max_tokens` 2500. Provider errors re-raise until the last attempt, then the session is `failed: generation_failed`; the failed call is billed independently so retries are not free (`metered_complete_json` gained the same failure billing `metered_complete` had). Everything after the model call is pure or best effort, so a retry never pays twice for the same scoring. A session whose job died is marked `failed: job_failed` when read (same as deck reviews). `reconcile` is a conditional `UPDATE ... WHERE status = the status it saw`, then a refresh, so a reader holding a session loaded as `ending` cannot overwrite a score the worker committed in the meantime (the same fix is applied to deck reviews). A `failed` session with a transcript is recoverable: `POST .../end` queues scoring again (the 10 answers are not lost); a purged or empty one is not. |
| Privacy | `summary()` (ratings, counts, durations) is the only shape a team member gets; `detail()` (transcript, scorecard) is founder only. Tests assert the founder's words never appear in a team member's response. The "summary ready" notification goes to founders only (`readiness.qa.scored`, category health_assessment). |
| Retention | Scheduled task `scheduled.readiness.qa_retention`, one global sweep a day (like the Documents trash purge). Sessions started over 90 days ago have `transcript` and `context` cleared and `purged_at` set, **and the scorecard loses its text** (questions, answers, feedback, model answer, all of which are the founder's words or derived from them): it keeps only the ratings, the average, which answers were picked and `text_purged: true` (`strip_scorecard_text`). Sessions left `active` for over a day are closed. |
| Score | New Story signal `story.mock_qa` (weight 2): the latest scored session's average rating out of 5; target 3 to 4 by stage; 0 with a "run a mock session" note when none. `readiness.qa.scored` refreshes the score immediately (it bypasses the 15 minute throttle) and is in the worker's trigger set. History `log` gets `qa_session` rows and `markers` "Mock Q&A scored (Good)". |

**What's involved**

| Area | Paths |
|---|---|
| Model, migration | `app/db/models/readiness.py` (`MockSession`), `alembic/versions/0093_readiness_mock_qa.py` |
| Service, job | `app/services/readiness/qa.py`, `app/worker/handlers/readiness.py` (`handle_qa_scoring`), `app/worker/scheduler.py` + `app/worker/handlers/scheduled.py` (retention) |
| API | `app/api/v1/endpoints/readiness.py`, `app/schemas/readiness.py`: `POST`/`GET /readiness/qa-sessions`, `POST .../{id}/messages`, `POST .../{id}/end`, `GET .../{id}` |
| Score, history, notifications | `app/services/readiness/signals.py`, `triggers.py`, `service.py`; notification registry, categories, audience |
| Shared | `app/platform/llm_budget.py` (`metered_complete_json` bills failed calls) |

**Verification:** `tests/services/readiness/test_qa.py` (scoring maths and picks, prompt fencing, context, start gates, cap, end, scoring success, failures and retries, privacy shape, purge, score and history wiring), `tests/api/test_readiness_qa_api.py` (all 7 roles on start, answer, end, list and read; the team member sees no words; full flow to the cap; tenancy; validation; budget), `tests/worker/test_readiness_qa_retention.py` (daily schedule, handler), `tests/platform/test_llm_budget.py`, migration round trip (`tests/test_readiness_migration.py`), live journey `e2e/test_readiness_mock_qa.py` (investor was the server's stub model, scorer scripted; captures in `e2e/_captures/readiness_qa/`). Not verified: a real model's questions, ratings and cost; the daily purge job running on the worker; the 10-answer cap against the live server (covered by API tests).

**Operate / roll back:** no new env var. The worker must run the handlers (registered in `app/worker/__main__.py`'s import list via `handlers.readiness` and `handlers.scheduled`). Roll back by downgrading `0093` (drops `mock_sessions`) and reverting the PR; the Story signal reads the table, so revert both together.

**Follow-ups**

- A real-model run to measure the 35k estimate and to tune the persona prompts; per-turn context could be trimmed if it runs heavy.
- Accepted risk: the model's per-answer ratings feed `story.mock_qa`, so answer text could try to inflate the rating. The 69 cap, the prompt fence and ratings clamped to 1..5 bound it; revisit if Story ever gates a decision on its own.
- Sessions are never deleted, only their transcript cleared; add a delete if founders ask.
- "Pause and resume tomorrow" is not offered: a session stays `active` for 2 hours at most before it stops blocking a new one.
