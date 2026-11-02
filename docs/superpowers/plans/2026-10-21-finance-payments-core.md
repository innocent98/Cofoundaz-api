# Finance Payments Core (Paystack + Flutterwave) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a founder connect their own Paystack or Flutterwave account (one per workspace), import the provider's NGN transactions into the Finance ledger, put a "Pay now" link on sent invoices, and mark invoices paid from signed provider webhooks.

**Architecture:** A `PaymentProvider` protocol (`app/services/finance/payments/`) with two adapters over `httpx`. Founder secrets are Fernet-encrypted at rest (`FINANCE_CONNECTIONS_ENCRYPTION_KEY`) and never returned. Import is idempotent through a unique `(connection_id, external_ref)` index; sync runs on demand and from the existing scheduler, guarded by row locks (`FOR UPDATE SKIP LOCKED`). Webhooks are public per-connection routes that verify the signature against the founder's own secret before parsing anything, and dedupe on a `provider_events` table.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres, `cryptography` Fernet, `httpx` (`httpx.MockTransport` for fixtures; `respx` is not a dependency and is not added).

**Spec:** `/Users/adebayovictor/Documents/dev/cofoundaz/cofoundaz/docs/checklist/finance-prd-gaps.md` (FE `origin/develop`): rows 4, 5, 6 (BE parts), "Build order" 1(a), D-FIN-2, D-FIN-6, D-FIN-7, D-FIN-9. Decisions from Adebayo: both providers now, one active connection per workspace, founders paste their own keys.

## Global Constraints

- Money is integer kobo (`amount_minor`). NGN only (D-FIN-6): non-NGN provider rows are skipped and counted, never imported.
- Only **founders** connect / replace / disconnect (D-FIN-2). Founder + team_member + accountant (`FINANCE_ROLES`) read status and trigger sync. Every other role gets 403.
- The secret key (and Flutterwave secret hash) is never returned, logged, put in an audit row, or put in an error message. Only `key_last4`, `mode`, `account_name` are exposed.
- `FINANCE_CONNECTIONS_ENCRYPTION_KEY` unset or invalid: connect/replace/webhook/sync return `503 FINANCE_CONNECTIONS_NOT_CONFIGURED`; `GET /finance/integrations` still works with `encryption_configured: false`.
- Imported rows: gross charge = `in`, fee = `out` in category `Payment fees`, refund = `out` (D-FIN-7). Read-only except `category`.
- A charge whose reference equals an invoice's `pay_reference` is never imported as a second inflow; it marks the invoice paid instead.
- Provider HTTP is only ever exercised with handwritten fixtures (`tests/fixtures/payments/`). No real or test keys, no real network.
- Copy: no em or en dashes in any user-facing string.
- Commits: no AI attribution, no `Co-Authored-By`, no "Generated with" footer. Branch pushed as `feature/finance-payments-core`.
- Run tests only against scratch DBs: `TZ=UTC DATABASE_URL=postgresql://user:password@localhost:5432/ci_scratch_fin TEST_DATABASE_URL=...ci_scratch_fin_test` (create them first; never the dev DB).

## Review Focus

- Amount over int32 kobo (provider charge above NGN 21,474,836.47): skipped and counted (`skipped_oversize`), never a 500. Test in Task 5.
- Webhook delivered twice, or concurrently: second is acked with `duplicate: true` and moves no money. Test in Task 6.
- Webhook signed with another workspace's key, with an empty/missing header, or with a non-ASCII header value: 401, no TypeError. Test in Task 6.
- Webhook amount or currency differs from the invoice total: invoice stays `sent`, event recorded `ignored`, 200 ack. Test in Task 6.
- Refund for a charge we never imported, or a partial refund: recorded as its own out row by refund id, no crash. Test in Task 5.
- Connect with a public key (`pk_...`) or wrong-provider prefix: 422 before any network call. Test in Task 4.
- A paid-by-provider invoice cannot be marked unpaid (sync would re-pay it). Test in Task 6.

---

### Task 1: Foundation (config, crypto, models, migration 0064)

**Files:**
- Modify: `app/core/config.py`, `.env.example`, `app/core/errors.py`, `app/db/models/enums.py` (`TransactionSource`: replace `stripe` with `paystack`, add `flutterwave`; add `ConnectionStatus`, `PaymentProvider` enums), `app/db/models/finance.py` (add `connection_id`), `app/db/models/invoice.py`, `app/db/models/__init__.py`, `tests/test_config.py` if it enumerates settings
- Create: `app/db/models/finance_connection.py`, `app/services/finance/payments/__init__.py`, `app/services/finance/payments/crypto.py`, `alembic/versions/0065_finance_payments_core.py`, `tests/services/finance/test_payments_crypto.py`, `tests/test_finance_payments_migration.py`

**Interfaces:**
- Produces settings: `FINANCE_CONNECTIONS_ENCRYPTION_KEY: str | None`, `API_PUBLIC_URL: str = ""`, `PAYMENT_PROVIDER_STUB: bool = False`, `FINANCE_SYNC_BACKFILL_DAYS: int = 90`, `FINANCE_SYNC_MAX_PAGES: int = 20`, `FINANCE_SYNC_MIN_INTERVAL_SECONDS: int = 30`.
- Produces error `FinanceConnectionsNotConfigured(AppError)`: code `FINANCE_CONNECTIONS_NOT_CONFIGURED`, 503, message `Payment connections are not configured on the server.`
- Produces `crypto.encrypt_secret(plain: str) -> str`, `crypto.decrypt_secret(token: str) -> str` (raises `FinanceConnectionsNotConfigured` when key unset/invalid; a bad token raises `FinanceConnectionUnreadable`, 500, `FINANCE_CONNECTION_UNREADABLE`).
- Produces models. `FinanceConnection` (table `finance_connections`): `id`, `startup_id` FK cascade, `public_id` String(43) unique (token_urlsafe(32)), `provider` String(16), `status` String(16) in `active|needs_attention|disconnected`, `key_ciphertext` Text null, `secret_hash_ciphertext` Text null, `key_last4` String(4), `mode` String(8) `test|live`, `account_name` String(200) null, `sync_cursor` DateTime(tz) null, `last_sync_at` DateTime(tz) null, `last_sync_summary` JSONB null, `last_error` String(300) null, `connected_by_user_id` UUID null, timestamps. Unique `(startup_id, provider)`; partial unique index on `startup_id` where `status <> 'disconnected'` (one live connection per workspace). `ProviderEvent` (`provider_events`): `id`, `connection_id` FK cascade, `event_id` String(200), `event_type` String(80), `outcome` String(24), timestamps; unique `(connection_id, event_id)`.
- Transactions: new `connection_id` UUID FK `finance_connections.id` ON DELETE SET NULL, null; partial unique index `uq_transactions_connection_ref` on `(connection_id, external_ref)` where `connection_id IS NOT NULL`. Reuse the existing `external_ref` column as the provider reference.
- Invoices: new nullable `pay_url` Text, `pay_reference` String(120) (indexed), `pay_provider` String(16), `paid_via` String(16).

- [ ] **Step 1:** Write failing tests: crypto round trip; unset key raises `FinanceConnectionsNotConfigured`; token garbage raises `FinanceConnectionUnreadable`; migration test (pattern: `tests/test_finance_migration.py`) asserts single head, `0065_finance_payments_core` in history, up/down/up round trip, and that the partial unique index rejects two non-disconnected connections for one startup.
- [ ] **Step 2:** Run them, expect FAIL.
- [ ] **Step 3:** Implement settings/errors/crypto/models/enums. Check whether `0039` created a CHECK constraint on `transactions.source`; if so the migration must drop and recreate it with the new values. Migration revision `0065_finance_payments_core`, `down_revision = "0064_sales_coach"` for now (rechained to `0063_sales_crm_core` in Task 8). Add `FINANCE_CONNECTIONS_ENCRYPTION_KEY=` to `.env.example` with a one-line Fernet generation comment, and to the CI pytest step only if a test needs it (tests set it with `monkeypatch`).
- [ ] **Step 4:** Run tests plus `alembic check` on the scratch DB (single head, no drift); expect PASS.
- [ ] **Step 5:** Commit `feat(finance): payment connections foundation and migration 0064`.

### Task 2: PaymentProvider interface + Paystack adapter

**Files:**
- Create: `app/services/finance/payments/base.py`, `app/services/finance/payments/paystack.py`, `app/services/finance/payments/registry.py`, `app/services/finance/payments/http.py`, `tests/fixtures/payments/paystack_*.json`, `tests/services/finance/test_payments_paystack.py`

**Interfaces:**
- Produces in `base.py`: frozen dataclasses `Credentials(secret_key: str, secret_hash: str | None)`, `AccountInfo(account_name: str | None)`, `ProviderTransaction(kind: Literal["charge","fee","refund"], reference: str, amount_minor: int, currency: str, occurred_at: datetime, description: str, parent_reference: str | None)` (a charge with a fee yields a separate `fee` item with reference `<ref>:fee`; refunds use `<refund-id>`), `PaymentLink(url: str, reference: str)`, `WebhookEvent(event_id: str, kind: Literal["charge","refund","other"], reference: str | None, amount_minor: int | None, currency: str | None, fee_minor: int | None, occurred_at: datetime | None)`. Errors: `ProviderAuthError`, `ProviderUnavailable`, `ProviderBadResponse` (all `Exception`, never embedding keys). `Protocol PaymentProvider` with `name`, `key_prefixes`, `mode_for(key) -> Literal["test","live"]`, `verify_key(creds) -> AccountInfo`, `list_transactions(creds, *, since: datetime | None, max_pages: int) -> list[ProviderTransaction]` (ascending by time), `create_payment_link(creds, *, reference, amount_minor, email, currency, description, redirect_url) -> PaymentLink`, `verify_webhook(creds, *, raw_body: bytes, headers: Mapping[str, str]) -> bool`, `parse_webhook(raw_body: bytes) -> WebhookEvent`.
- Produces `registry.get_provider(name: str) -> PaymentProvider` (raises `NotFound` for unknown) and `http.http_client() -> httpx.Client` (timeout 15s; when `settings.PAYMENT_PROVIDER_STUB` and `ENVIRONMENT != "production"` it uses the stub transport from Task 4, otherwise the real transport). Adapters take `client: httpx.Client | None` in their constructor so tests inject `httpx.MockTransport`.

- [ ] **Step 1:** Write failing tests using handwritten fixtures (Paystack `GET /balance`, paged `GET /transaction`, `GET /refund`, `POST /transaction/initialize`, a 401 body, a 500). Assert: `mode_for("sk_test_x") == "test"`, `"sk_live_x" -> "live"`; `key_prefixes == ("sk_test_", "sk_live_")`; kobo amounts pass through unchanged; a charge with `fees` yields a charge and a fee item; non-success status rows are dropped; pagination follows `meta.pageCount` and stops at `max_pages`; 401 raises `ProviderAuthError`; 5xx/timeout raises `ProviderUnavailable`; `verify_webhook` is true only for `x-paystack-signature == HMAC-SHA512(raw_body, secret_key)` hex (case-insensitive header lookup, `hmac.compare_digest` on bytes, false for missing/empty/non-ASCII header); `parse_webhook` of `charge.success` gives `event_id == "charge.success:<data.id>"`, kind `charge`; `refund.processed` gives kind `refund`; unknown event gives `other`.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement `base.py`, `paystack.py` (base URL `https://api.paystack.co`, `Authorization: Bearer <key>`, `GET /transaction?status=success&perPage=100&page=N&from=<iso>`), registry, http factory. Money as `int`; never float. The Authorization header must never appear in exception text or logs.
- [ ] **Step 4:** Run, expect PASS. Also run mypy on the new package.
- [ ] **Step 5:** Commit `feat(finance): PaymentProvider interface and Paystack adapter`.

### Task 3: Flutterwave adapter

**Files:**
- Create: `app/services/finance/payments/flutterwave.py`, `tests/fixtures/payments/flutterwave_*.json`, `tests/services/finance/test_payments_flutterwave.py`
- Modify: `app/services/finance/payments/registry.py`

**Interfaces:** implements `PaymentProvider` from Task 2. Base URL `https://api.flutterwave.com/v3`.

- [ ] **Step 1:** Failing tests with fixtures (`GET /balances` for key check, paged `GET /transactions?status=successful`, refunds listing, `POST /payments`). Assert: `key_prefixes == ("FLWSECK_TEST-", "FLWSECK-")`, `mode_for("FLWSECK_TEST-x") == "test"`; Flutterwave amounts are **major units**, converted to kobo with `Decimal` (`1234.56 -> 123456`, never `float`); `app_fee` becomes a `fee` item; `verify_webhook` is true only when the `verif-hash` header equals `creds.secret_hash` via `hmac.compare_digest` on bytes; false when header missing, empty, differs, or `creds.secret_hash` is empty/None; non-ASCII header does not raise; `parse_webhook` of `charge.completed` with `data.status == "successful"` gives kind `charge`, reference `tx_ref`, `event_id == "charge.completed:<data.id>"`; a non-successful status gives `other`.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement and register `flutterwave`; `create_payment_link` posts major-unit amount and returns `data.link`.
- [ ] **Step 4:** Run, expect PASS; mypy.
- [ ] **Step 5:** Commit `feat(finance): Flutterwave adapter`.

### Task 4: Connections service + `/finance/integrations` routes

**Files:**
- Create: `app/services/finance/payments/connections.py`, `app/services/finance/payments/stub_transport.py`, `app/schemas/finance_connection.py`, `tests/api/test_finance_integrations.py`
- Modify: `app/api/v1/endpoints/finance.py` (or a new `finance_integrations.py` router included under `/finance`, preferred to keep files small)

**Interfaces:**
- Consumes: Task 1 models/crypto, Task 2/3 registry.
- Produces: `connections.connect(db, *, startup_id, user_id, provider, secret_key, secret_hash) -> FinanceConnection`, `connections.disconnect(db, *, startup_id, user_id, provider) -> FinanceConnection`, `connections.get_active(db, startup_id) -> FinanceConnection | None`, `connections.credentials(conn) -> Credentials`, `connections.serialize(conn) -> ConnectionResponse` (`id, provider, status, mode, key_last4, account_name, last_sync_at, last_error, last_sync_summary, webhook_url, created_at`). `webhook_url = f"{API_PUBLIC_URL or SERVER_HOST:SERVER_PORT}{API_V1_STR}/webhooks/payments/{provider}/{public_id}"`.
- Produces `stub_transport.StubTransport` (an `httpx.BaseTransport`) serving deterministic handwritten Paystack and Flutterwave responses for key check, listing, link creation; any key containing `bad` returns the provider's 401 body.
- Routes (envelope via `success_response`): `GET /finance/integrations` (FINANCE_ROLES) returns `{encryption_configured, providers: [{provider, label, key_hint, connection|null}]}` for both providers; `POST /finance/integrations/{provider}/connect` body `{secret_key, secret_hash?}` (founder only); `POST .../sync` (FINANCE_ROLES, Task 5 fills the body; here return 501-free stub is NOT allowed, so define the route in Task 5); `POST .../disconnect` (founder only).
- Rules: prefix check before any network call (422 field `secret_key`); Flutterwave requires `secret_hash` (422 field `secret_hash`); another provider active returns 409 `PAYMENT_PROVIDER_ALREADY_CONNECTED` ("Disconnect {Other} before connecting {This}."); same provider active means replace (resets `sync_cursor`, status `active`, rotates nothing else); a previously disconnected row for the same `(startup, provider)` is reactivated (same row id keeps import idempotency) with a **new** `public_id`; provider rejects the key: 422 `PAYMENT_KEY_REJECTED`; provider down: 502 `PAYMENT_PROVIDER_UNAVAILABLE`. Disconnect wipes both ciphertexts, sets `disconnected`, keeps imported rows. Audit via `write_audit` actions `finance.connection.connected|replaced|disconnected`, `entity_type="finance_connection"`, no key material anywhere.

- [ ] **Step 1:** Failing API tests: founder connects Paystack (stubbed via `monkeypatch` of the registry's http client to a `MockTransport`) and the response contains no `secret_key`, no `key_ciphertext`, and `key_last4`/`mode`; the raw key string is absent from `response.text`, from the `audit_log` rows and from captured log output; DB column holds a Fernet token (starts `gAAAA`) not the key; replace; disconnect keeps transactions; 409 for the other provider; `pk_` key 422; Flutterwave without hash 422; unset encryption key 503 on connect and 200 with `encryption_configured: false` on GET; **403 matrix**: team_member, accountant on connect/disconnect; mentor, consultant (and any non-finance role) on every route including GET; 401 unauthenticated.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement service, schemas, routes, stub transport. Use `with db.begin_nested()` around inserts that can violate the unique indexes (add inside the savepoint), mapping `IntegrityError` to the 409.
- [ ] **Step 4:** Run, expect PASS; black/isort/ruff/mypy on touched files.
- [ ] **Step 5:** Commit `feat(finance): payment connections API with encrypted keys`.

### Task 5: Import + sync (on demand and scheduled)

**Files:**
- Create: `app/services/finance/payments/importer.py`, `app/services/finance/payments/sync.py`, `app/worker/handlers/finance_payments.py`, `tests/services/finance/test_payments_import.py`, `tests/services/finance/test_payments_sync.py`, `tests/api/test_finance_sync.py`
- Modify: `app/services/finance/service.py` (`_reject_managed`: for sources `paystack|flutterwave`, DELETE rejected and PATCH allowed only for `category`, 422 on any other field, message `Imported transactions are read-only except the category.`), `app/worker/scheduler.py`, the worker handler registration module (follow how `scheduled.py` handlers are imported), finance integrations router (sync route)

**Interfaces:**
- Produces `importer.import_items(db, *, connection, items: Sequence[ProviderTransaction]) -> ImportResult` where `ImportResult` has `imported, duplicates, invoice_matched, skipped_currency, skipped_oversize, fees, refunds` (ints). Rules: non-NGN skipped; `amount_minor > 2_147_483_647` or `<= 0` skipped (oversize counted); charge whose `reference` equals any `Invoice.pay_reference` in the startup calls `invoices.mark_paid_via_provider` (Task 6) and counts `invoice_matched` instead of inserting an inflow; otherwise insert `Transaction(source=<provider>, direction=in, category="Revenue", external_ref=reference, connection_id=...)`; fee: `out`, category `Payment fees`; refund: `out`, category `Refunds`. Insert inside `db.begin_nested()` with `add()` inside the savepoint; `IntegrityError` on `(connection_id, external_ref)` counts as duplicate.
- Produces `sync.sync_connection(db, connection_id, *, now: datetime | None = None, manual: bool = False) -> SyncOutcome | None`: selects the connection `with_for_update(skip_locked=True)` (None means another worker holds it, skip); only `active` connections; manual calls within `FINANCE_SYNC_MIN_INTERVAL_SECONDS` of `last_sync_at` raise `AppError("SYNC_TOO_SOON", ..., 429)`; first run backfills `FINANCE_SYNC_BACKFILL_DAYS`, later runs start at `sync_cursor` minus 1 day overlap; stops at `FINANCE_SYNC_MAX_PAGES`; on success sets `sync_cursor` (max `occurred_at`), `last_sync_at`, `last_sync_summary`, clears `last_error`; `ProviderAuthError` sets status `needs_attention` and `last_error="The provider rejected the saved key. Replace it to resume syncing."`; `ProviderUnavailable` sets only `last_error` (status stays `active`); after importing anything calls `runway_svc.evaluate_runway_alert` and `upsert_runway_signal`.
- Produces route `POST /finance/integrations/{provider}/sync` (FINANCE_ROLES; 404 when no active connection for that provider) returning `{connection, summary}`.
- Scheduler: `SCHED_PAYMENTS_SYNC = "scheduled.finance.payments_sync"`; `_due_payment_syncs(db, now)` returns one `Due("payments.sync", str(conn.id), <local date>T<hour>, ...)` per `active` connection per local hour, payload `{connection_id, startup_id}`, added to `scheduler_tick`'s list; handler `handle_payments_sync` calls `sync_connection` and is registered with `register_handler`.

- [ ] **Step 1:** Failing tests: first import creates charge in + fee out rows with exact kobo; re-running the same items imports 0 (idempotent); non-NGN counted `skipped_currency`; int32 overflow counted `skipped_oversize` with no exception; refund for unknown parent and a partial refund each create one out row keyed by refund id; charge matching an invoice `pay_reference` does not create an inflow (stub `mark_paid_via_provider` until Task 6, then assert the invoice is `paid` with one inflow total); imported row PATCH `category` ok, PATCH `amount_minor` 422, DELETE 422; sync updates cursor/summary; `ProviderAuthError` leads to `needs_attention`; two sessions: while one holds the lock the other `sync_connection` returns None; manual sync twice quickly gives 429 `SYNC_TOO_SOON`; scheduler enqueues exactly once per connection per hour and not for `disconnected` / `needs_attention`; sync route 403 for mentor, 200 for founder, team_member, accountant; run with an `autoflush=False` session variant for the importer (see memory: prod `SessionLocal` is autoflush=False).
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement as specified.
- [ ] **Step 4:** Run, expect PASS; lint.
- [ ] **Step 5:** Commit `feat(finance): idempotent transaction import and scheduled sync`.

### Task 6: Invoice pay links + public webhooks

**Files:**
- Create: `app/api/v1/endpoints/payment_webhooks.py`, `app/services/finance/payments/webhooks.py`, `app/services/finance/payments/paylinks.py`, `tests/api/test_payment_webhooks.py`, `tests/api/test_invoice_pay_links.py`
- Modify: `app/services/finance/invoices.py` (add `mark_paid_via_provider`, guard `mark_unpaid`, serialize new fields), `app/schemas/invoice.py` (add `pay_url`, `pay_provider`, `paid_via`), `app/api/v1/endpoints/finance.py` (`send` creates the link; new `POST /invoices/{id}/pay-link`), `app/worker/handlers/invoice_email.py` (link in email), `app/api/v1/api.py` (include webhook router with no auth dependency, prefix `/webhooks`)

**Interfaces:**
- `paylinks.ensure_pay_link(db, *, invoice) -> bool`: no-op false unless the invoice is `sent`, currency NGN, an `active` connection exists, and the provider call succeeds; sets `pay_reference = f"{number}-{uuid4().hex[:8]}"`, `pay_url`, `pay_provider`; `redirect_url = {APP_BASE_URL or SERVER_HOST}/finance/invoices`. Provider failure never fails `send`: `pay_url` stays null, log a warning without secrets. `POST /finance/invoices/{id}/pay-link` (FINANCE_ROLES) retries it and returns the invoice; 422 `NO_PAYMENT_CONNECTION` when none; reuses the existing link if present.
- `invoices.mark_paid_via_provider(db, *, startup_id, invoice_id, provider: str, now) -> Invoice | None`: reuses the locked `mark_paid` path, sets `paid_via=provider`; idempotent.
- `invoices.mark_unpaid` raises 422 (field `status`, "This invoice was paid through {Provider} and cannot be marked unpaid.") when `paid_via` is set.
- Route `POST /webhooks/payments/{provider}/{connection_public_id}` (public, mounted under `/api/v1`): read raw bytes (reject over 256 KB with 413); look up connection by `(provider, public_id)` with status `active` or `needs_attention` (unknown: 404 `NOT_FOUND`); decrypt creds; **verify signature on the raw bytes before any JSON parsing**, failure is 401 `INVALID_SIGNATURE`; then parse; insert `ProviderEvent` in a savepoint (duplicate `(connection_id, event_id)` returns 200 `{received: true, duplicate: true}` and does nothing else); `charge` with `pay_reference` match: lock invoice, require `amount_minor == total_minor` and currency `NGN` else outcome `ignored` (200); else `mark_paid_via_provider`, outcome `invoice_paid`; `charge` without invoice match: `importer.import_items` of the charge (+ fee if present), outcome `imported`; `refund`: import as refund row; `other`: outcome `ignored`. Always 200 `{received: true}` after a verified event so providers stop retrying. No secrets or raw bodies in logs.

- [ ] **Step 1:** Failing tests. Pay link: send with active stubbed connection stores `pay_url`/`pay_reference`/`pay_provider`, email HTML contains the escaped link, provider failure leaves send successful with `pay_url: null`, `pay-link` retry works, 422 without connection, 403 for mentor. Webhooks (both providers): valid Paystack signature marks invoice `paid` once with exactly one `Transaction(source=invoice)`; same event twice returns `duplicate: true`, one inflow; concurrent duplicate (two threads/sessions) still one inflow; wrong key signature 401; missing, empty and non-ASCII signature header 401; Flutterwave wrong or empty `verif-hash` 401; tampered body 401; unknown public id 404; amount mismatch leaves invoice `sent`; disconnected connection id 404; unconfigured encryption key 503; verified event for a non-invoice charge imports one inflow; `mark-unpaid` on provider-paid invoice 422; a manual `mark-paid` after webhook stays a no-op.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement as specified.
- [ ] **Step 4:** Run, expect PASS; lint; `bandit` and `semgrep`-style checks later in Task 8.
- [ ] **Step 5:** Commit `feat(finance): invoice pay links and signed payment webhooks`.

### Task 7: Live e2e journey + FE integration guide

**Files:**
- Create: `e2e/test_finance_payments.py`, `docs/fe-integration-guide-finance-payments.md`, committed captures under `e2e/_captures/finance_payments/`
- Modify: `scripts/e2e_run.sh` (export `PAYMENT_PROVIDER_STUB=true`, `FINANCE_CONNECTIONS_ENCRYPTION_KEY` generated like `MFA_ENCRYPTION_KEY`, `FINANCE_SYNC_MIN_INTERVAL_SECONDS=0`, `API_PUBLIC_URL`), `.github/workflows/live-e2e.yml` only if it needs the same env

- [ ] **Step 1:** Write the journey (pattern `e2e/test_invoices.py`, captures via the existing helper): founder onboards; invites/creates a team member and accountant if the harness supports it, else exercises the 403s with the roles it has; GET integrations (empty); connect with bad key (422); connect Paystack; secret absent in every capture; second provider 409; sync (stub listing) then ledger shows charge + fee rows with `source: "paystack"`; PATCH category ok / amount 422 / DELETE 422; create and send an invoice, `pay_url` present, email file contains link; webhook with valid HMAC-SHA512 computed by the test marks it paid; duplicate webhook; bad signature 401; mark-unpaid 422; disconnect keeps ledger; connect Flutterwave with secret hash and a `verif-hash` webhook (failing and passing). Every response body saved.
- [ ] **Step 2:** Run `scripts/e2e_run.sh` against scratch DBs only (override `E2E_DB`, `E2E_ADMIN_DB`, `E2E_PORT` to unused values; never the dev DB or the docker stack). Expect all pass.
- [ ] **Step 3:** Write `docs/fe-integration-guide-finance-payments.md` per the global rules: every body pasted verbatim from the captures, nesting traps, error table with exact strings, polling/state guidance (`active`, `needs_attention`, `disconnected`; sync cadence), UX instructions (key never shown again, test badge, replace needs re-entry, Flutterwave hash, webhook URL to paste in the provider dashboard, `needs_attention` banner, "Paid via" tag, oversize/currency skips), and a verification table that marks provider-side behaviour (real Paystack/Flutterwave responses, real webhook delivery, refund payload shapes) as **not verified live: shapes from handwritten fixtures**.
- [ ] **Step 4:** Commit `test(finance): payments e2e journey and FE integration guide`.

### Task 8: Docs, CI parity, rechain, PR

**Files:**
- Create: `docs/sop/2026-10-21-finance-payments-core.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md` (Module 12 rows), `docs/fe-integration-guide-finance-invoices.md` (new invoice fields, `mark-unpaid` rule), `docs/fe-integration-guide-finance-cashflow.md` only if transaction shape changed, `alembic/versions/0065_finance_payments_core.py` (down_revision)

- [ ] **Step 1:** Write SOP (what/why/how/involved/verification/operate-rollback/follow-ups; follow-ups: needs_attention banner and notification, replacing int32 amount column, key custody in KMS, live sandbox walk) and checklist rows.
- [ ] **Step 2:** `git fetch origin`. If `origin/develop` contains `0063_sales_crm_core`: merge develop and set `down_revision = "0063_sales_crm_core"`. Else set it anyway, and base the PR on `feature/sales-crm-core` if that branch exists on origin, otherwise note it in the PR body and base on `develop`. Verify `alembic heads` shows one head once 0063 is present.
- [ ] **Step 3:** Run every CI gate locally via `poetry run`: black --check, isort --check-only, ruff check, mypy app, full pytest with coverage `--cov-fail-under=95` (scratch DBs, `TZ=UTC`, `JOURNAL_ENCRYPTION_KEY` generated), alembic single head / upgrade / check / downgrade base / upgrade, gitleaks, semgrep, bandit and pip-audit as `ci.yml` defines them (read the remaining job steps). Fix until green.
- [ ] **Step 4:** `git push -u origin HEAD:feature/finance-payments-core`; open the PR (no AI attribution anywhere); do not merge. Bind the PR with the ccd_pr tools.
