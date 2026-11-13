# Funding Hub: term sheets (slice D)

**What shipped:** the API behind the Funding Hub **Term sheets** page (PRD 14.5): enter an offer by hand or upload a PDF, let a job propose the terms, confirm them, see deterministic "unusual for this stage" flags, and compare 2 to 6 offers side by side. **Founder only**, API only. FE guide `docs/fe-integration-guide-funding-term-sheets.md`. Branch `feature/funding-term-sheets`. Migration **`0102_funding_term_sheets`** (parent `0103_readiness_narrative`; 0101 is reserved for Marketplace C, so the lead re-chains if it lands first). Also folds in two lows from the cap table review (below).

**Why:** the mock had two fixed columns of hard-coded values and one hard-coded AI sentence (gap rows 29 to 32). Design and calls: `cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md` section 6; D-FUND-5 (a) founder only; D-FUND-6 (b) a deterministic rules table decides the flags, the AI only extracts values (which the founder confirms) and words each flag.

**How**

| Topic | Decision |
|---|---|
| Founder only (D-FUND-5) | `TERM_SHEET_ROLES = CAP_TABLE_ROLES = {founder}`; every route uses `require_role` (403 for team member and all others), then `assert_feature("funding")` (402 behind `PLAN_ENFORCEMENT`). The notification audience is the same set. |
| Typed vs extracted | A typed sheet is `confirmed` on save. An uploaded PDF is `extracting` -> `needs_confirmation` -> `confirmed` (or `failed`). The model's output lives in `proposed` and **nothing counts until the founder confirms**: only confirmed sheets have `terms`/`flags`, can be compared or edited, or are announced. A DB CHECK ties `status = 'confirmed'` to `confirmed_at`. |
| Rules table (D-FUND-6) | `services/funding/term_rules.py` is the policy a reviewer reads: seven rules (liquidation preference above 1x, participating preferred, option pool above 15% of the pre-money, exclusivity above 45 days, more than one investor board seat on a pre-seed or seed round, super pro-rata, full ratchet). Thresholds are strictly "above". An unstated term never flags; the board seats rule needs `stage` and does not guess. The wording is fixed per rule, built from the sheet's own values, and never a verdict ("unusual for this stage"); a test scans it for verdict words and dashes. |
| Extraction | Job `ai.funding.term_sheet_extract` (counts against the daily AI allowance). `pypdf` text (reusing the deck reader), capped at 28k characters, sent inside a fence with an instruction to treat it as data. The model reports whole-currency money, percent and multiples; we convert to minor units, basis points and hundredths, and validate **each field on its own** so one bad value drops only that field. It also returns a verbatim quote per field, kept only for fields that survived (shown beside the value at confirmation). The model also reports the currency the document states; `proposed.currency` is kept as an ISO code. If it differs from the round's, the detail carries `currency_mismatch`, `document_currency`, `round_currency`, and confirming money terms without `acknowledge_currency_mismatch: true` is 409 `CURRENCY_MISMATCH` (we never convert). The fence closing tag is stripped in any case or spacing. After a cap on non-participating preferred is dropped, the rest is re-validated so numbers come out as integers. A PDF with under 200 characters of text fails as `no_text` without a model call. Same bytes already extracted (not failed) return that sheet (`deduplicated`), spending nothing. A sheet whose job died is reconciled to `failed` (`job_failed`), as the deck review does. |
| Wording | Job `ai.funding.term_sheet_wording`, optional. It rewords only the flags already raised; a rewording is kept only if its figures open with the fixed text's headline figure and follow the fixed text's order (dropping a secondary figure is fine; swapping, repeating or inventing one is not), so the model cannot move a figure. The write is made under the sheet's row lock: the job refreshes the row `FOR UPDATE` after the model returns, compares the flags it reworded with the current ones, and drops the result if they differ, so a founder edit that committed first is never overwritten and one that arrives later waits. Failure or an exhausted allowance leaves the fixed text; the FE shows `ai_text` when present, else `text`. |
| Comparison | `term_compare.py`: one column per sheet (2 to 6, confirmed only), one row per term at least one sheet states, `null` for a sheet that does not. `best`/`worst` follow a plain founder-side ordering (higher pre-money, lower preference, none < standard < super pro-rata, ...) and stay empty on ties, a single stater, or unranked terms. `investor_ownership_bps = investment / (pre + investment)`, half-even to a basis point; `post_money_minor` is derived. |
| Notification | `funding.term_sheet.added` on confirmation or a typed save: "Term sheet added, comparison updated", body carries the not-advice line, audience founders minus the actor. |
| Audit | `term_sheet_audit`: create, confirm, update, delete with before and after (terms and the rule ids that flagged), actor and wall-clock time. The worker's own state changes (extraction finished or failed) are `update` rows with no actor; a PATCH that changes nothing writes none. Kept apart from the platform `audit_log` because offers are founder-only data. |
| Concurrency | The PDF is read and hashed before the workspace lock is taken (storage I/O does not hold the workspace), and duplicates are re-checked under it. Failed sheets do not count toward the 200-sheet cap. Every write takes the workspace lock (`services/funding/locks.py`) and the sheet row `FOR UPDATE`, so a double-clicked confirm confirms once (the second is 409 `NOT_AWAITING_CONFIRMATION`). |
| Currency (D-FUND-7) | Term sheet amounts are in the round currency. Any sheet stating a pre-money or investment now also fixes the round currency, with the cap table's amounts (`rounds.py`; error code kept as `CAP_TABLE_HAS_AMOUNTS`, message names term sheets). |
| Rejected | Letting the model decide flags (D-FUND-6 a); showing the proposal as the sheet; per-sheet currency; storing the PDF text; a verdict score for the offer. |

**Folded in from the cap table review (lows):** a round created without a currency now carries the previous round's currency over (after a closed USD round with amounts, the next round is USD instead of 409); a duplicated solver line in `cap_engine.py` removed.

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/term_sheet.py` (`TermSheet`, `TermSheetAudit`), `app/db/models/__init__.py`, `alembic/versions/0102_funding_term_sheets.py` |
| Schemas | `app/schemas/term_sheet.py` |
| Services | `app/services/funding/{term_rules,term_compare,term_sheets,term_extract,locks}.py`, `rounds.py` (currency) |
| Worker | `app/worker/handlers/funding.py`, registered in `app/worker/__main__.py` |
| API | `app/api/v1/endpoints/funding_term_sheets.py`, mounted at `/funding` in `app/api/v1/api.py`; role set `TERM_SHEET_ROLES` in `enums.py` |
| Notifications | `app/services/notifications/{registry,audience}.py` |

Endpoints (all under `/api/v1/funding/term-sheets`): `GET /`, `POST /`, `POST /extract`, `GET /rules`, `GET /compare?ids=`, `GET /audit`, `GET/PATCH/DELETE /{id}`, `POST /{id}/confirm`.

**Verification:** `tests/services/funding/test_term_rules.py` (every threshold at and above, stage dependence, order, wording scanned for verdicts), `test_term_compare.py` (columns, best/worst, ties, half-even ownership), `test_term_extract.py` (unit conversion, per-field validation, quotes, fence, wording number guard); `tests/api/test_funding_term_sheets_api.py` (every route refused for each non-founder role with nothing changed, 402 gate, cross-workspace 404, typed and extracted flows, confirm semantics, dedupe, failure paths, allowance, wording stale/failed/moved-number, comparison, audit, lock, currency); live journey `e2e/test_funding_term_sheets.py` (25 captures, the source of the FE guide; the model is canned). Local: black, isort, ruff, mypy, bandit; migration upgrade, downgrade, upgrade and `alembic check` on a fresh scratch DB. CI runs the full suite.

**Operate / roll back:** no env var and no new secret; AI spend is the extraction (about 10k tokens in, 1k out per sheet) and, only when flags exist, the wording (about 3k), against the existing 100k/day/workspace budget. The worker must run the two new handlers (imported in `app/worker/__main__.py`). Revert by downgrading `0102` (drops the two tables) and reverting the PR.

**Follow-ups**

- A real model's extraction accuracy has not been exercised; the confirm step and the quotes are the control. Worth a manual run with a few real term sheets before the FE ships.
- "Ask a Legal Advisor to review" is a pure FE link to Documents e-review.
- Attaching a sheet to an investor card's drawer is `investor_id` + `GET /term-sheets?investor_id=`; the pipeline overview does not yet count term sheets.
- Rules are the PRD's starting set; adding one is a row in `term_rules.py` plus its tests.

**Opus review fixes (2026-11-12):** document currency detection and the acknowledgement (medium); wording job writes under the row lock (medium); coercion after dropping the cap; storage read before the lock; case-insensitive fence strip; system audit rows and no row for a no-op PATCH; swapped-figure rewording discarded; failed sheets outside the cap.
