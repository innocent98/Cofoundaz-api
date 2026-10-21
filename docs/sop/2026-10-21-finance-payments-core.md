# SOP: Finance payments core (Paystack + Flutterwave)

| | |
|---|---|
| **What shipped** | Finance BE slice (a): gap rows 4, 5, 6 (BE) of `cofoundaz/docs/checklist/finance-prd-gaps.md`. A founder connects their own Paystack or Flutterwave account; NGN transactions import into the ledger; sent invoices get a pay link; signed provider webhooks mark invoices paid. Branch `feature/finance-payments-core`, migration `0065_finance_payments_core`, stacked on `feature/sales-coach-analytics` (`0064_sales_coach`). PR: see the checklist entry. |
| **Why** | Finance had no payment provider: no stored credentials, no import (a re-sync would double count), no pay link, `mark-paid` manual only. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| `PaymentProvider` protocol, two `httpx` adapters (`paystack.py`, `flutterwave.py`); registry by name | founders choose one provider; FE and services never branch on provider | one hard-coded Paystack client |
| One live connection per workspace: partial unique index on `startup_id` where `status <> 'disconnected'`; unique `(startup_id, provider)`; reconnect reuses the row (new `public_id`) | keeps import idempotency across disconnect/reconnect; switching provider needs an explicit disconnect (409 otherwise) | a row per connect |
| Keys Fernet-encrypted with `FINANCE_CONNECTIONS_ENCRYPTION_KEY`; unset gives 503 `FINANCE_CONNECTIONS_NOT_CONFIGURED`; GET status still works. Only `key_last4`, `mode` (from the key prefix), `account_name` are returned. Disconnect wipes ciphertexts. Audit rows `finance.connection.connected/replaced/disconnected` carry no key material | D-FIN-9 | returning a masked key; KMS (later hardening) |
| Money in kobo; Flutterwave major units converted with `Decimal`, never float; only `NGN` imported, others and amounts above int32 are skipped and counted | D-FIN-6; `transactions.amount_minor` is a 32-bit integer | converting currencies; widening the column (follow-up) |
| Ledger rows (D-FIN-7): charge = `in` "Revenue", fee = `out` "Payment fees" (`<ref>:fee`), refund = `out` "Refunds"; `external_ref` is the provider reference; unique `(connection_id, external_ref)`; imported rows are read-only except `category` | idempotent re-sync; no double count | upsert by overwrite |
| A charge whose reference equals an invoice `pay_reference` and whose amount/currency match marks that invoice paid (one inflow) instead of importing a second inflow; an underpaying charge is a plain inflow and the invoice stays `sent` | no double counting, no part-payment settling a full invoice | trusting the reference alone |
| Sync: `FOR UPDATE SKIP LOCKED` on the connection row, cursor with 1-day overlap, 90-day first backfill, page cap, manual calls throttled (`SYNC_TOO_SOON`); scheduler claims one job per active connection per local hour; a rejected key flips the connection to `needs_attention` | row locks, safe concurrent workers | one global sync job |
| Webhooks: public `POST /api/v1/webhooks/payments/{provider}/{public_id}`; signature checked on the raw bytes before any parsing (Paystack HMAC-SHA512 with the founder key; Flutterwave `verif-hash` equals the founder secret hash; `hmac.compare_digest`); dedupe on `provider_events (connection_id, event_id)` in the same transaction as the effect; invoice row locked; amount/currency mismatch is acked and ignored | idempotent, race-safe | global webhook with a shared secret |
| Pay link created on `send` (never fails the send), retry endpoint `POST /finance/invoices/{id}/pay-link`; provider-paid invoices cannot be marked unpaid | sync would re-pay them | |

## What's involved
- Migration `0065_finance_payments_core`: tables `finance_connections`, `provider_events`; `transactions.connection_id`; invoice `pay_url`, `pay_reference`, `pay_provider`, `paid_via`; `TransactionSource` gains `paystack`, `flutterwave` (the old `stripe` value is replaced; nothing wrote it).
- Code: `app/services/finance/payments/` (`base, paystack, flutterwave, registry, http, stub_transport, crypto, connections, importer, sync, paylinks, webhooks`), `app/api/v1/endpoints/finance_integrations.py`, `payment_webhooks.py`, `finance.py` (send, pay-link), `app/services/finance/{invoices,service}.py`, `app/worker/scheduler.py` + `handlers/finance_payments.py`, `handlers/invoice_email.py`.
- Config: `FINANCE_CONNECTIONS_ENCRYPTION_KEY`, `API_PUBLIC_URL`, `PAYMENT_PROVIDER_STUB` (ignored in production), `FINANCE_SYNC_BACKFILL_DAYS/MAX_PAGES/MIN_INTERVAL_SECONDS`.
- Routes: `GET /finance/integrations`, `POST /finance/integrations/{provider}/connect|sync|disconnect`, `POST /finance/invoices/{id}/pay-link`, public webhook route. Roles: connect/disconnect founder only; status, sync, pay-link `FINANCE_ROLES`; everyone else 403.

## Verification
- Unit: provider adapters on handwritten fixtures (`tests/fixtures/payments/`), connections incl. no-leak and the 403 matrix, importer (also under `autoflush=False`), sync lock with two real sessions, scheduler, webhooks (signature negatives, duplicates, races, crash retry), pay links.
- Live e2e on a real server with the stub provider transport: `e2e/test_finance_payments.py`, 61 captures in `e2e/_captures/finance_payments/`, feeding `docs/fe-integration-guide-finance-payments.md`.
- **Not verified against real Paystack/Flutterwave**: all response shapes, real webhook delivery and signatures, refund payloads, the Flutterwave payment-link amount sent as a string. A sandbox check with Adebayo's test keys is planned in the FE walk.

## Operate / roll back
- Generate the key: `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`, set `FINANCE_CONNECTIONS_ENCRYPTION_KEY` per environment (losing it makes stored keys unreadable: founders must reconnect). Set `API_PUBLIC_URL` so the webhook URL shown to founders is the public one.
- Roll back: `alembic downgrade 0064_sales_coach` (drops the tables/columns; imported ledger rows keep their data but lose `connection_id`).

## Follow-ups
- The webhook shares the default 120/min per-IP rate limit; providers send from few IPs, so consider a higher limit for that route.
- A manual `mark-paid` before the webhook leaves `paid_via` empty, so it can still be marked unpaid.
- `needs_attention` banner and notification (row 7), widen `amount_minor`, KMS key custody, live sandbox verification.
