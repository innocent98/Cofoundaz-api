# SOP: Journal PRD gaps (BE)

**What shipped:** the API behind the whole Journal page as designed: read today's entry by date, richer list (stress, preview, word
count, month filter, month index), stats, mood range + roadmap annotations, rule-based insights and the supportive card, prompt dismissal +
theme library, journal settings, an optional passcode lock enforced by the API, export, permanent delete, milestone retros, and the opt-in
evening reminder / daily prompt notifications. Migration `0080_journal_gaps`. Branch `feature/journal-be-gaps`. Closes J1 to J12, J13
(API half), J15 in `cofoundaz/docs/checklist/journal-prd-gaps.md`. FE guide: `docs/fe-integration-guide-journal.md` (rewritten from live captures).

## Why

The page was mostly a mock (fake PIN, hardcoded stats, a Delete button that deleted nothing). The API had create / list / read / update /
delete, a mood trend and today's prompt only, so most designed controls had no endpoint. This is the FE hand-off pass (Adebayo 2026-10-27).

## How: decisions and rejected alternatives

| Topic | Decision |
|---|---|
| **D-JNL-1 passcode** | Optional 4 to 12 digit passcode, bcrypt-hashed in `journal_settings`. A screen lock enforced by the API: while set, every content route needs `X-Journal-Token` (HS256 JWT (sub, sid, ver, purpose, exp), 30 min, key derived from `SECRET_KEY` and the purpose so it can never verify as a login token) or answers `423 JOURNAL_LOCKED`. Changing, removing or "Lock journal" bumps `passcode_version`, killing outstanding tokens. Unlocking locks for 15 minutes after 5 wrong tries, doubling every further 5 (cap 24 h), reset by a success (counter committed before the error is raised). Tokens also bind the workspace. The account-password check on `DELETE /passcode` shares the Settings 5-strikes / 15-minute Redis throttle (found by the Opus review, fixed). Forgotten passcode: removed with the ACCOUNT password. Rejected: client-side encryption keyed by the PIN (forgot-PIN would destroy entries; server features like search and export would die); a UI-only lock (the current fake). Entries stay Fernet-encrypted with the server key, so the UI copy was corrected (J16). |
| **D-JNL-3 no AI over text** | Insights read `(date, mood, stress)` plus milestone dates only (`services/journal/stats.py` never imports `decrypt_content`). Rules: weekday stress, deadline stress, mood shift, streak, great days. Rejected: an AI Reflections tab (contradicts PRD "journal content is never retrievable by any agent"). |
| **Supportive card** | Last 14 days hold 7+ entries and all are rough/meh; dismissal hides it for 30 days. The PRD says "mood <= 2 for 14+ days": we use a 14-day window with at least 7 entries so a sparse writer can still see it. Stored only as `supportive_dismissed_at`. |
| **D-JNL-4 retros/library** | API only; no designed screen. Retro answers are one Fernet token; a dismissed offer is a row without content (so it never returns). The milestone title is snapshotted. "Completed" is `roadmap_milestones.updated_at` for a `done` milestone (no completion timestamp column exists), offered for 30 days. |
| **D-JNL-5 export** | One JSON file of decrypted entries + retros, passcode-gated, `Cache-Control: no-store`, audited (`journal.exported`, no counts or text). |
| **Delete** | `DELETE /journal {"confirm":"DELETE"}` removes entries, mood logs, retros, prompts and settings (incl. the passcode) in one transaction; audited. |
| **Dates** | The client sends its local date; the API accepts up to one day ahead. Rejected: a stored timezone per founder (the notification preferences hold one, but journal dates must not depend on a server-side guess). |
| **Notifications** | `journal.reminder` (20:00 local, skipped when an entry exists) and `journal.prompt.daily` (09:00), opt-in per founder, in-app only (no email category), generic copy, deep link `/journal`. The scheduler reads `journal_settings` and only checks that an entry row exists. Registered as founder-only in `AUDIENCE`. |
| **List performance** | Search still decrypts in memory; now capped to the newest 2,000 entries. |
| **Bug fixed** | `PATCH {"stress": null}` named `content`; it now names `stress`. |

## What's involved

`app/api/v1/endpoints/journal.py` (rewritten route set), `app/services/journal/{lock,stats,retros,account,prompt_library}.py`,
`service.py` (by-date, month filter, search cap), `app/db/models/journal.py` (`JournalSettings`, `JournalRetro`, `JournalPrompt.dismissed`),
`app/schemas/journal.py`, `app/services/notifications/{registry,audience}.py`, `app/worker/{scheduler,handlers/scheduled}.py`, migration
`0080_journal_gaps` (chained after `0077`; Billing `0078` and Calendar `0079` merge first, re-chain before merging).
Tests: `tests/api/test_journal_gaps.py` (92), plus fixes to `test_journal_entries.py` and the two notification audience tests. Live journey:
`e2e/test_journal_gaps.py`, captures in `e2e/_captures/journal_gaps/` (the older `journal/` captures were re-run).

## Verification

Unit: all journal, notification and scheduler tests green on fresh scratch Postgres DBs; black, isort, ruff and mypy clean; migration
upgrade / downgrade round trip and `alembic check` clean. Live: `e2e/test_journal_gaps.py` and `e2e/test_journal.py` passed against a real
uvicorn on a scratch DB, including the scheduler-driven reminder reaching `GET /notifications`. Not live: the 5-try lockout, token expiry, the
supportive card appearing, the weekday/deadline/mood-shift insights, the 09:00 prompt notification (all unit-tested).

## Operate / roll back

No new env vars (the existing `JOURNAL_ENCRYPTION_KEY` is still required; staging lacks it, handoff 0c). `alembic downgrade 0077_settings_notif_schedule`
drops `journal_settings`, `journal_retros` and `journal_prompts.dismissed`; the API must be reverted first. A founder who set a passcode and lost it
uses `DELETE /journal/passcode` with their password.

## Follow-ups

Retros screen and prompt-library screen (need design); real-model prompt text; a per-day insight cache if stats get slow for very long journals;
rate limit on `/export`; `SECRET_KEY` rotation invalidates unlock tokens (acceptable, 30 min life).
