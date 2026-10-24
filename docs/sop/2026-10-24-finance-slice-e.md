# SOP — Finance BE slice (e): runway reads the model, invoice extras, Advisor context, XLSX, missing receipts

**What shipped.** Branch `feature/finance-slice-e`, migration `0071_finance_slice_e` (after `0070_finance_model_mono`). Closes the five API asks of the lead's Finance re-walk (FE #153 asks, defect D2). PR: see the PR for this branch. FE guide: `docs/fe-integration-guide-finance-slice-e.md`.

## Why
The re-walk confirmed four missing API pieces and one defect: runway could not show hiring/one-off rows (and `PUT` silently dropped them), invoices had no client picker, notes, payment details or Resend, the Advisor had no finance grounding, there was no XLSX export, and a legacy expense (`cbee3c6d…`) kept `has_receipt: true` while its file was gone, so the receipt link led to a 404.

## How (decisions and rejected alternatives)
| Item | Decision | Rejected |
|---|---|---|
| Runway rows (D-FIN-12) | The projection reads the newest **complete** model's `hiring_plan` and `one_offs` (founder edits applied) through `runway._model_rows` + `runway_math.monthly_rows`; response gains top-level `model_rows` `{model_id, href, hiring_plan, one_offs}`. `PUT /runway/assumptions` uses `extra="forbid"`, and `hiring_plan` / `one_offs` / `capex` are declared only to refuse them with a field-named 422 pointing at the Financial Model. Hires are not scaled by the best/worst cost multiplier; model `capex` is not read. | Storing runway's own rows (contradicts D-FIN-12). Reading a generating/failed model (a regeneration would blank the runway). |
| Invoice client | `account_id` FK to `sales_accounts` (`ON DELETE SET NULL`, an issued invoice outlives its account), validated to the caller's workspace; foreign and unknown ids give the **same** 422. `client_name`/`client_email` stay required copies. | Deriving name/email from the account (accounts have no email; an account rename must not rewrite an issued invoice). |
| Notes / payment details | `notes` Text (≤2000, blank→NULL), `payment_details` JSONB `{bank_name, account_name, account_number, instructions?}` with a strict schema. Draft-only edits, explicit `null` clears. Rendered in the PDF and (HTML-escaped) in the email. | Free-form JSON (unbounded, unvalidated). Editing after send (commercial terms are frozen). |
| Resend | `POST /invoices/{id}/resend` re-enqueues the existing `email.invoice_sent` job (same PDF/pay-link path). Sent (incl. derived overdue) only. Row lock + 60 s per-invoice cooldown (`last_sent_at`, 429 `RESEND_TOO_SOON`) **plus** slowapi `10/minute` per user. | Cooldown in Redis only (not durable, not per invoice). |
| Advisor | `ai_cofounder/finance_context.py`: numbers-only paragraph (cash, burn, revenue excl. Funding per D-FIN-11, runway, overdue count/total) appended to the system prompt in `handle_cofounder_reply`, only when the asker has an **active** membership with a role in `FINANCE_ROLES` (the set the Finance routes use). No client/vendor text, so nothing user-authored enters the prompt. No structured output, so no strict-schema walker test applies. | Giving the paragraph to every member (leak). Including invoice client names (prompt injection surface). |
| XLSX | **No new dependency**: `services/finance/xlsx.py` writes the workbook with `zipfile` (same trade-off as `invoice_pdf.py`). Text is always an inline string, never a formula (`=` cells are displayed, not evaluated). Routes: `GET /finance/transactions/export.xlsx` (same filters as the list, 50 000-row cap, newest first) and `GET /finance/model/{id}/export.xlsx` (P&L, Cash flow, Balance sheet). Finance roles, `10/minute`, `no-store`. Amounts are naira numbers. | openpyxl (new image dependency and CVE surface). |
| D2 receipt | `receipt_link` checks the local file; if gone it **clears the receipt fields** (self-heal) and answers **410** `RECEIPT_FILE_MISSING`, committing the clear. Later calls are an ordinary 404 and the expense can take a new upload. | Only returning an error without clearing (the expense would lie forever). Cleaning in a batch job (needs a run; the lazy heal needs none). |

## What's involved
* Migration `alembic/versions/0071_finance_slice_e.py`: `invoices.account_id` (FK + index), `notes`, `payment_details`, `last_sent_at`. Reversible; downgrade drops the four columns. `alembic check` clean.
* `app/db/models/invoice.py` (columns + `account` relationship, `selectin` so `FOR UPDATE` locks stay legal), `app/schemas/invoice.py`, `app/services/finance/invoices.py` (`_require_account`, `_apply_client_extras`, `resend_invoice`), `invoice_pdf.py`, `app/worker/handlers/invoice_email.py`.
* `app/schemas/finance_runway.py`, `app/services/finance/runway.py`, `runway_math.py`.
* `app/services/finance/exports.py`, `xlsx.py`, `service.py` (`filtered_query`/`ordered` shared by list and export), `app/api/v1/endpoints/finance.py` (resend, two exports, 410 handling).
* `app/services/ai_cofounder/{finance_context,prompts}.py`, `app/worker/handlers/ai.py`.
* `app/services/finance/expenses.py` (`ReceiptFileMissing`).

## Verification
* Unit: full suite 3998 passed, coverage 97.00% (gate 95). New: `tests/api/test_finance_slice_e.py` (31), `tests/worker/test_cofounder_finance_context.py` (13), `tests/test_finance_slice_e_migration.py` (2).
* Live: `e2e/test_finance_slice_e.py` against real uvicorn on a fresh migrated DB; full e2e 92 passed. Captures in `e2e/_captures/finance_slice_e/` back the FE guide.
* Local CI parity: black, isort, ruff, mypy, alembic (one head, upgrade, check, downgrade base, upgrade), bandit, pylint 9.88, radon, semgrep and gitleaks (results in the PR).
* **Not verified live**: Cloudinary receipts (the missing-file check only knows local storage), the Advisor's model reply (stub LLM), the XLSX/resend rate limit over real time, opening the XLSX in Excel/Numbers itself (parsed as XML only).

## Operate / roll back
* Deploy: run migrations (`0071`). No new dependency, no env var, no image rebuild beyond the normal deploy.
* Rollback: `alembic downgrade 0070_finance_model_mono` drops the four invoice columns (notes, payment details and client links are lost). Revert the code first, since the old code does not read them.
* Behaviour change to announce to FE: `PUT /finance/runway/assumptions` now rejects `hiring_plan`, `one_offs`, `capex` and unknown fields (422) instead of ignoring them.

## Follow-ups
* FE: runway "from your model" block, invoice picker/notes/bank fields/Resend button, XLSX buttons, 410 handling in `ReceiptButton`.
* Cloudinary receipts have no missing-file detection.
* Runway ignores model `capex` rows; say so if Adebayo wants capex counted.
* `model_xlsx` uses monthly statements only; a yearly sheet could be added.
