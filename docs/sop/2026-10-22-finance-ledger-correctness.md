# SOP: Finance ledger correctness (private receipts, categories, budgets from the ledger, pagination)

| | |
|---|---|
| **What shipped** | Finance BE slice (c): gap rows 2, 3, 15, 17, 18, 19, 28, 29, 30 of `cofoundaz/docs/checklist/finance-prd-gaps.md`. Branch `feature/finance-ledger-correctness`, migration `0067_finance_ledger_correctness` (after `0066`, now on `develop`). PR: see the checklist entry. |
| **Why** | Receipts were public Cloudinary URLs (anyone with the link reads a financial document; investors must never see raw ledgers); upload type trusted the client; model generation could be fired repeatedly against the AI budget; categories were case-sensitive free text so budgets missed spend; budgets ignored manual outflows; lists were unbounded; any currency and zero amounts were accepted. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| Receipts stored as Cloudinary `type="authenticated"` raw assets (local: files under `LOCAL_STORAGE_DIR`, never mounted). API exposes only `GET /finance/expenses/{id}/receipt` (same `FINANCE_ROLES` check and workspace scoping as the expense; foreign id = 404) | a stable URL cannot be revoked; a route can be role-checked every time | public `upload` assets with an obscure id; streaming every file through the API (loads the API for large PDFs, still needs the signed form for `<img>`) |
| The route returns JSON `{url, expires_at, content_type}`, TTL 300 s (`RECEIPT_LINK_TTL_SECONDS`). Cloudinary: `private_download_url(... type="authenticated", expires_at=...)`. Local: our own HMAC token (`app/platform/signed_urls.py`, key derived from `SECRET_KEY` with a purpose label) carrying the storage key, the owning `startup_id` and the expiry, on `GET /finance/receipts/download?token=`. The route checks the key shape, then that an expense of that workspace still holds that key, and serves it with `nosniff`, `no-store` and `Content-Security-Policy: sandbox; default-src 'none'`. Expired, tampered, wrong-workspace, replaced/removed and unknown tokens are the same 404 | the FE cannot send a bearer header from `<a href>`/`<img>`; the token is the credential and is only minted after the role check | JWT (new dependency surface); long TTL |
| Legacy rows: `expenses.receipt_private` (default false). Responses never include `receipt_url` (column kept, no longer written or read). First open of a legacy receipt calls `make_private` (Cloudinary rename `upload` to `authenticated`); if it fails the request errors instead of returning a public URL; a Cloudinary `NotFound` on the rename is treated as already moved (the rename succeeded but the commit did not). A one-off backfill (`scripts/privatise_legacy_receipts.py`, `services/finance/receipts_backfill.py`) moves them all in batches | no data migration against Cloudinary at deploy time; fail closed | rewriting all assets in the Alembic migration (network in a migration); leaving old URLs live |
| Upload type from magic bytes (PNG, JPEG, PDF at offset 0); extension and content type chosen from the sniff; client filename/type ignored; 10 MB cap; empty = 422 | HTML labelled `image/png` was accepted | trusting `Content-Type`, `python-magic` (native dependency) |
| `generate`: row lock on the workspace's `generating` rows, plus partial unique index `uq_financial_models_one_generating`; second call = `409 MODEL_ALREADY_GENERATING`; a `generating` row older than 15 min is failed ("Generation timed out") so a lost job cannot block forever | two layers: lock for the normal path, index as the backstop | returning the existing job (409 chosen so the FE shows "already running"); per-day cap (not asked) |
| Categories: resolve typed text to default spelling, else the workspace's oldest used spelling, else as typed (`services/finance/categories.py`); reads compare case-folded so old rows still match; `GET /finance/categories` = defaults + used | no data rewrite, no lowercasing of what users typed | lower-casing everything; a categories table (not needed yet) |
| D-FIN-3: budget actuals = all money-out ledger rows of the month grouped by case-folded category (`budgets.ledger_outflow_totals`); `draft-from-actuals` uses the same totals (uncategorised folds into "Other"); the Expenses page summary stays expense-only | one definition of spend | counting expenses plus manual rows separately |
| `GET /budgets` appends unbudgeted categories (`id`/`limit_minor` null, `unbudgeted: true`, `status: "unbudgeted"`; uncategorised spend as "Uncategorized"); new `variance_percent` and `status` (under / near from 80% / over) on all rows | PRD table needs them; wording for accessibility | hiding unbudgeted spend |
| Pagination: keyset on the existing sort (`date`/`expense_date`, `created_at`, `id`), `limit` 1..200 default 100, `meta {next_cursor, total_estimate}` (exact filtered count), bad cursor = 422. Invoices page in Python because status is derived | stable under inserts; same `Meta` envelope as other endpoints | offset paging |
| D-FIN-6: `ngn_only` on transactions, expenses, budgets (PydanticCustomError so the message is clean); `amount_minor > 0` on transactions and expenses (budget limit 0 stays valid). The Paystack/Flutterwave importer already skipped non-NGN and reported `skipped_currency` (slice a); verified, unchanged | | converting currencies |

## What's involved
- Migration `alembic/versions/0067_finance_ledger_correctness.py` (column, partial unique index, pre-step failing duplicate `generating` rows; downgrade drops both).
- `app/platform/storage.py` (private API on `LocalStorage` / `CloudinaryStorage`), `app/platform/signed_urls.py`, `app/services/finance/{expenses,categories,budgets,service,invoices,model_service}.py`, `app/schemas/{finance_rules,finance,expense,budget}.py`, `app/core/pagination.py`, `app/api/v1/endpoints/finance.py` (`GET /expenses/{id}/receipt`, `GET /receipts/download`, `GET /categories`, paginated lists).
- Tests: `tests/services/finance/test_receipts_backfill.py`, `tests/api/test_finance_receipts_private.py`, `test_finance_ledger_rules.py`, `test_finance_model_generate_guard.py`, `tests/platform/test_storage_private.py`; existing receipt/budget/expense tests updated for the new rules. E2E `e2e/test_finance_ledger_correctness.py` (33 captures in `e2e/_captures/finance_ledger/`); `e2e/test_expenses.py` and `e2e/test_budgets.py` updated.
- Docs: `docs/fe-integration-guide-finance-ledger.md` (new) plus superseded-in-part notes on the expenses, budgets, model, cashflow and invoices guides.
- Config: none new. `API_PUBLIC_URL` is used to build absolute local links (relative when empty).

## Verification
- Full unit suite on scratch Postgres: 3587 passed at 97.45% coverage (gate 95). Lint (black, isort, ruff, mypy, bandit, pylint 9.88 clean; migrations: single head, fresh upgrade, `alembic check`, downgrade to base and up again.
- Live e2e (own server, scratch DB): finance journeys pass, including receipts per role, legacy local row, tampered link, budgets, pagination, 409. One unrelated e2e (`test_marketing_prd_a`, a UTC-vs-local-time assertion) fails on this host only.
- **Not live-verified**: anything against real Cloudinary (authenticated upload, signed download URL, legacy rename), link expiry over real time (unit test with a controlled clock), two simultaneous generate calls over HTTP (unit test with two real sessions), investor 403 (investors cannot be invited yet; unit test).

## Operate / roll back
- Deploy: `alembic upgrade head`, then run the backfill once per environment (it needs that environment's Cloudinary credentials when `STORAGE_BACKEND=cloudinary`):

  ```bash
  docker compose exec api python -m app.services.finance.receipts_backfill
  ```

  (The image does not ship `scripts/`, so the old `python -m scripts.privatise_legacy_receipts` only works from a source checkout. Ran 2026-10-24: local `moved=1 failed=0`, staging `moved=0 failed=0`.)

  It prints `moved=N failed=M` (exit 1 when `M > 0`; re-run until `failed=0`). It is idempotent: it only selects `receipt_key IS NOT NULL AND receipt_private = false`, commits per batch of 100, and logs counts. **Until it has run (or a legacy receipt is opened) the old public Cloudinary URL still works**, so run it straight after deploy.
- Verify on staging with Cloudinary credentials: upload, fetch link, fetch file, open a legacy receipt, confirm the old `res.cloudinary.com/.../upload/...` URL stops working.
- Roll back: `alembic downgrade 0066_finance_invoice_automation` drops the column and index. Code before this slice would treat `authenticated` assets as having no `receipt_url`, so receipts uploaded after deploy would show `has_receipt: false`; do not roll back the code without a plan for those.

## Follow-ups
- Drop `expenses.receipt_url` once nothing reads it.
- Per-workspace daily cap on model generation (gap row 15 also suggested it) is not built.
- FE: Select + "Add category", budget table with variance and status words, "Load more" using `meta.next_cursor`, receipt open via the route, disabled Generate while running.
