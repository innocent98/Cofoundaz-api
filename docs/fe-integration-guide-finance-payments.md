# FE Integration Guide — Finance Hub: Payments (Paystack and Flutterwave)

> **Provenance.** `e2e/test_finance_payments.py::test_finance_payments_journey` was run against the
> live app (`bash scripts/e2e_run.sh` equivalent, scratch DB and port; **84 e2e passed**, 83 existing
> plus this journey). Every **response body** below is pasted **verbatim** from the captures it
> wrote to `e2e/_captures/finance_payments/` (named inline as `capture: <file>.json`); the rendering
> of this document inlines the capture files byte for byte, so nothing was typed from the schema,
> the DTO or memory. Nothing is trimmed and no field is invented.
>
> **What "live" means here, read this once.** The server was the real uvicorn app with
> `PAYMENT_PROVIDER_STUB=true`: calls to Paystack and Flutterwave (key check, transaction listing,
> payment-link creation) were answered by a deterministic, handwritten stub **inside the server**,
> not by the real providers. Webhooks were POSTed by the test to the real public webhook route,
> signed by the test itself (HMAC-SHA512 hex for Paystack, the stored secret hash for Flutterwave).
> So our routes, RBAC, encryption at rest, import, idempotency, signature checks and state changes
> are verified live. **Real provider response shapes, real webhook delivery and real signatures are
> NOT verified** (the verification table at the end lists each one). Provider payload shapes come
> from handwritten fixtures built from the providers' public docs.
>
> The harness captures **responses only**, so **request** bodies shown are the exact JSON the journey
> sends (read from `e2e/test_finance_payments.py`). All keys, hashes, ids, emails and the
> `webhook_url` token in the captures are **throwaway placeholders** generated per run (secret keys
> look like `sk_test_<32 hex>`); the run asserted that none of the secrets it sent appears in any
> captured body. In the examples below, wherever a request needs a key, write your own **placeholder**
> such as `sk_test_PLA_CEHOLDER` (never paste a real key into docs, tickets or logs).

## What the feature is (in the FE's terms)

A founder opens **Finance, Integrations** and connects **one** payment account, **Paystack or
Flutterwave**, by pasting their own secret key. After that:

1. Their provider's **NGN transactions are imported** into the Finance ledger (a Sync button, plus
   an hourly background sync). Charges become income, provider fees become expenses.
2. Every **sent NGN invoice gets a "Pay now" link**, which is also in the client email. When the
   client pays, the provider calls our **webhook** and the invoice is marked **paid** automatically.

Base path `/api/v1`. Finance routes need `Authorization: Bearer <token>` and `X-Workspace-Id`. Every
success response is the envelope `{"data": …, "meta": null}`; every error is
`{"error": {"code", "message", "field_errors"}}`. The webhook route is **public and is called by the
provider, never by the FE**; the FE only shows its URL.

| # | Route | Who | Purpose |
|---|---|---|---|
| 1 | `GET /finance/integrations` | founder, team_member, accountant | both providers + the one live connection (if any) |
| 2 | `POST /finance/integrations/{provider}/connect` | **founder only** | connect or replace (`paystack` or `flutterwave`) |
| 3 | `POST /finance/integrations/{provider}/sync` | founder, team_member, accountant | import now |
| 4 | `POST /finance/integrations/{provider}/disconnect` | **founder only** | disconnect (keeps imported rows) |
| 5 | `POST /finance/invoices/{id}/pay-link` | founder, team_member, accountant | create or retry the pay link |
| 6 | `POST /webhooks/payments/{provider}/{public_id}` | public (provider only) | not an FE route |

Any other role (mentor, consultant, investor, legal advisor) gets **403 `FORBIDDEN`** on routes 1 to 5
(verified live for mentor).

---

## Read this first: the traps that produce a wrong-looking screen

**(1) The secret key is never returned. Ever.** Only `key_last4`, `mode` and `account_name` come
back. After connecting, the FE cannot show or re-fill the key. To change it the user must **type it
again** (Replace). Show `key_last4` as `••••1493` style text. Never store the pasted key in FE state,
local storage or analytics after the request returns.

**(2) The Flutterwave secret hash is a second, required field.** `secret_hash` is the value the user
typed into Flutterwave dashboard, Settings, Webhooks, "Secret hash". Without it Flutterwave connect
is 422 on field `secret_hash`. It is also never returned. Paystack ignores it.

**(3) One live connection per workspace.** Connecting the other provider while one is live is
**409 `PAYMENT_PROVIDER_ALREADY_CONNECTED`**; the user must disconnect first. The 409 `message` is
display-ready. Connecting the **same** provider again is a **replace**, not an error.

**(4) `connection` is `null` for the provider that is not connected, and `GET /integrations` always
lists both providers** in the fixed order `paystack`, `flutterwave`. There is never a list with one
entry. Render from `providers[]`, not from "is there a connection".

**(5) Nesting.** `POST …/sync` returns `{connection, summary}`: the same counts appear twice,
once as `connection.last_sync_summary` and once as top-level `summary`. Use `summary` for the toast
and `connection` to refresh the card. `last_sync_summary` and `last_sync_at` are **`null`** (present,
not absent) until the first sync finishes.

**(6) `webhook_url` is an absolute URL the user must paste into the provider dashboard.** Show it
with a Copy button on the connected card. It is built from the server's public base URL (here
`http://127.0.0.1:8041`, the e2e server; in staging and production it is whatever the deployment
sets, not verified here). It is **unguessable**, tied to the connection, **unchanged by Replace**,
and stops working on Disconnect (404). Webhooks are what mark invoices paid, so a connected card
without the webhook pasted into the provider dashboard still imports on sync but invoices will
not auto-mark as paid; say so in the card.

**(7) Imported ledger rows are read-only except `category`.** They appear in
`GET /finance/transactions` with `"source": "paystack"` or `"flutterwave"`. Hide edit and delete,
leave the category dropdown. The list endpoint has **no `source` filter**; filter on the client.

**(8) A provider-paid invoice cannot be marked unpaid** (422). Hide the "Mark unpaid" action when
`paid_via` is set.

**(9) Pay links exist only for `sent` NGN invoices while a connection is `active`.** A link is not
created for drafts, other currencies, or when nothing is connected. `pay_url` may be `null` on a sent
invoice; offer **Create pay link** (route 5) then.

---

## The connection object

Returned by connect, disconnect, inside `sync`, and as `providers[].connection`.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | stable across Replace, Disconnect and reconnecting the same provider ⚠️ (reconnect: code and unit test, not captured) |
| `provider` | `"paystack"` \| `"flutterwave"` | |
| `status` | string | `active` \| `needs_attention` \| `disconnected` (see state machine) |
| `mode` | `"test"` \| `"live"` | from the key prefix (`sk_test_` / `FLWSECK_TEST-` = test). Show a **Test mode** badge |
| `key_last4` | string | last 4 characters of the key |
| `account_name` | string \| null | business name the provider reported. `null` in the live run (the stub reports none); ⚠️ real providers may fill it |
| `last_sync_at` | ISO datetime \| null | `null` until first sync |
| `last_error` | string \| null | display-ready text when there is a problem |
| `last_sync_summary` | object \| null | counts of the last sync (below) |
| `webhook_url` | string | absolute URL to paste into the provider dashboard |
| `created_at` | ISO datetime | |

`summary` / `last_sync_summary` keys (all integers): `imported` (new income rows), `fees` (new fee
rows), `refunds`, `duplicates` (already imported, skipped), `invoice_matched` (charges that paid one
of our invoices), `skipped_currency` (non-NGN rows ignored), `skipped_oversize` (amount too large or
not positive, ignored), `fetched` (rows the provider returned).

---

## 1. `GET /finance/integrations`

Empty workspace. `capture: integrations_empty.json`

```json
{
  "data": {
    "encryption_configured": true,
    "providers": [
      {
        "provider": "paystack",
        "label": "Paystack",
        "key_hint": "Starts with sk_test_ or sk_live_",
        "connection": null
      },
      {
        "provider": "flutterwave",
        "label": "Flutterwave",
        "key_hint": "Starts with FLWSECK_TEST- or FLWSECK-",
        "connection": null
      }
    ]
  },
  "meta": null
}
```

`encryption_configured: false` means the server has no encryption key set: connect, replace, sync
and webhooks then return **503 `FINANCE_CONNECTIONS_NOT_CONFIGURED`**; the GET still works. Show
"Payments are not available right now" and disable Connect. ⚠️ Not verified live (the e2e server
has the key set); behaviour is unit-tested.

`key_hint` is display text for the key field placeholder or helper.

Paystack connected (after step 2 below). `capture: integrations_paystack_connected.json`

```json
{
  "data": {
    "encryption_configured": true,
    "providers": [
      {
        "provider": "paystack",
        "label": "Paystack",
        "key_hint": "Starts with sk_test_ or sk_live_",
        "connection": {
          "id": "419732d3-6f58-48da-a468-a939691b8487",
          "provider": "paystack",
          "status": "active",
          "mode": "test",
          "key_last4": "eac6",
          "account_name": null,
          "last_sync_at": null,
          "last_error": null,
          "last_sync_summary": null,
          "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
          "created_at": "2026-10-21T13:06:58.716100Z"
        }
      },
      {
        "provider": "flutterwave",
        "label": "Flutterwave",
        "key_hint": "Starts with FLWSECK_TEST- or FLWSECK-",
        "connection": null
      }
    ]
  },
  "meta": null
}
```

Roles: an accountant and a team member also get 200 (verified live, bodies not captured). A mentor:
`capture: error_integrations_mentor_forbidden.json`

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Unauthenticated: `capture: error_integrations_unauthenticated.json`

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

## 2. `POST /finance/integrations/{provider}/connect` (founder only)

Request (Paystack), with your own **placeholder** key:

```json
{ "secret_key": "sk_test_PLA_CEHOLDER" }
```

Request (Flutterwave):

```json
{ "secret_key": "FLWSECK_TEST-PLA_CEHOLDER-X", "secret_hash": "PLACEHOLDER_SECRET_HASH" }
```

Response 200. `capture: connect_paystack.json`

```json
{
  "data": {
    "id": "419732d3-6f58-48da-a468-a939691b8487",
    "provider": "paystack",
    "status": "active",
    "mode": "test",
    "key_last4": "eac6",
    "account_name": null,
    "last_sync_at": null,
    "last_error": null,
    "last_sync_summary": null,
    "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
    "created_at": "2026-10-21T13:06:58.716100Z"
  },
  "meta": null
}
```

The server checks the key prefix locally first (no network), then asks the provider to verify the key.
Replace is the same call with a new key; `id` and `webhook_url` stay the same, `status` returns to
`active` and `last_error` is cleared. `capture: connect_paystack_replaced.json`

```json
{
  "data": {
    "id": "419732d3-6f58-48da-a468-a939691b8487",
    "provider": "paystack",
    "status": "active",
    "mode": "test",
    "key_last4": "10e0",
    "account_name": null,
    "last_sync_at": "2026-10-21T13:06:58.795565Z",
    "last_error": null,
    "last_sync_summary": {
      "fees": 0,
      "fetched": 2,
      "refunds": 0,
      "imported": 0,
      "duplicates": 2,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0
    },
    "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
    "created_at": "2026-10-21T13:06:58.716100Z"
  },
  "meta": null
}
```

Connect Flutterwave (after the Paystack connection was disconnected). `capture: connect_flutterwave.json`

```json
{
  "data": {
    "id": "628e9941-4448-4794-8fb3-0dbf8d8be4e9",
    "provider": "flutterwave",
    "status": "active",
    "mode": "test",
    "key_last4": "75-X",
    "account_name": null,
    "last_sync_at": null,
    "last_error": null,
    "last_sync_summary": null,
    "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/flutterwave/qDeolzDYuBmJM9Pvv_2jgDghqSE1rmu8UguT_uvWcmY",
    "created_at": "2026-10-21T13:07:00.320075Z"
  },
  "meta": null
}
```

### Connect errors

| Case | Status | `code` | Exact `message` / `field_errors` | Capture |
|---|---|---|---|---|
| Public key (`pk_…`) | 422 | `VALIDATION_ERROR` | `Please check the highlighted fields.` field `secret_key`: `Use your Paystack secret key (starts with sk_test_ or sk_live_).` | `error_connect_public_key.json` |
| Other provider's key on this provider | 422 | `VALIDATION_ERROR` | same as above (field `secret_key`) | `error_connect_wrong_provider_prefix.json` |
| Flutterwave without hash | 422 | `VALIDATION_ERROR` | field `secret_hash`: `Enter your Flutterwave secret hash.` | `error_connect_flutterwave_missing_hash.json` |
| Provider rejects the key | 422 | `PAYMENT_KEY_REJECTED` | `Paystack did not accept that key. Check it and try again.` (`field_errors` is `[]`) | `error_connect_key_rejected.json` |
| Other provider already live | 409 | `PAYMENT_PROVIDER_ALREADY_CONNECTED` | `Disconnect Paystack before connecting Flutterwave.` | `error_connect_other_provider_conflict.json` |
| Unknown provider name | 404 | `NOT_FOUND` | `Not found.` | `error_connect_unknown_provider.json` |
| team_member / accountant / mentor | 403 | `FORBIDDEN` | `You don't have permission to do that.` | `error_connect_team_member_forbidden.json`, `error_connect_accountant_forbidden.json`, `error_connect_mentor_forbidden.json` |
| Provider down | 502 | `PAYMENT_PROVIDER_UNAVAILABLE` | `We could not reach Paystack. Try again in a few minutes.` | ⚠️ not verified live (message read from `connections.py`) |
| Encryption key not set on server | 503 | `FINANCE_CONNECTIONS_NOT_CONFIGURED` | ⚠️ not verified live, exact message not captured | unit test |

Pasting a key with surrounding whitespace is trimmed. The 422s with `field_errors` should be shown
under the matching field; `PAYMENT_KEY_REJECTED` and the 409 are form-level messages.

Bodies, for reference. `capture: error_connect_public_key.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "secret_key",
        "message": "Use your Paystack secret key (starts with sk_test_ or sk_live_)."
      }
    ]
  }
}
```

`capture: error_connect_flutterwave_missing_hash.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "secret_hash",
        "message": "Enter your Flutterwave secret hash."
      }
    ]
  }
}
```

`capture: error_connect_key_rejected.json`

```json
{
  "error": {
    "code": "PAYMENT_KEY_REJECTED",
    "message": "Paystack did not accept that key. Check it and try again.",
    "field_errors": []
  }
}
```

`capture: error_connect_other_provider_conflict.json`

```json
{
  "error": {
    "code": "PAYMENT_PROVIDER_ALREADY_CONNECTED",
    "message": "Disconnect Paystack before connecting Flutterwave.",
    "field_errors": []
  }
}
```

`capture: error_connect_team_member_forbidden.json`

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

## 3. `POST /finance/integrations/{provider}/sync`

No request body. The journey calls it as an accountant (allowed). First run backfills recent history,
later runs continue from where the last stopped. Response 200. `capture: sync_paystack_first.json`

```json
{
  "data": {
    "connection": {
      "id": "419732d3-6f58-48da-a468-a939691b8487",
      "provider": "paystack",
      "status": "active",
      "mode": "test",
      "key_last4": "eac6",
      "account_name": null,
      "last_sync_at": "2026-10-21T13:06:58.756716Z",
      "last_error": null,
      "last_sync_summary": {
        "fees": 1,
        "fetched": 2,
        "refunds": 0,
        "imported": 1,
        "duplicates": 0,
        "invoice_matched": 0,
        "skipped_currency": 0,
        "skipped_oversize": 0
      },
      "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
      "created_at": "2026-10-21T13:06:58.716100Z"
    },
    "summary": {
      "imported": 1,
      "duplicates": 0,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0,
      "fees": 1,
      "refunds": 0,
      "fetched": 2
    }
  },
  "meta": null
}
```

Running it again imports nothing new, because rows are de-duplicated. `capture: sync_paystack_again.json`

```json
{
  "data": {
    "connection": {
      "id": "419732d3-6f58-48da-a468-a939691b8487",
      "provider": "paystack",
      "status": "active",
      "mode": "test",
      "key_last4": "eac6",
      "account_name": null,
      "last_sync_at": "2026-10-21T13:06:58.795565Z",
      "last_error": null,
      "last_sync_summary": {
        "fees": 0,
        "fetched": 2,
        "refunds": 0,
        "imported": 0,
        "duplicates": 2,
        "invoice_matched": 0,
        "skipped_currency": 0,
        "skipped_oversize": 0
      },
      "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
      "created_at": "2026-10-21T13:06:58.716100Z"
    },
    "summary": {
      "imported": 0,
      "duplicates": 2,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0,
      "fees": 0,
      "refunds": 0,
      "fetched": 2
    }
  },
  "meta": null
}
```

Flutterwave. `capture: sync_flutterwave.json`

```json
{
  "data": {
    "connection": {
      "id": "628e9941-4448-4794-8fb3-0dbf8d8be4e9",
      "provider": "flutterwave",
      "status": "active",
      "mode": "test",
      "key_last4": "75-X",
      "account_name": null,
      "last_sync_at": "2026-10-21T13:07:00.363645Z",
      "last_error": null,
      "last_sync_summary": {
        "fees": 1,
        "fetched": 2,
        "refunds": 0,
        "imported": 1,
        "duplicates": 0,
        "invoice_matched": 0,
        "skipped_currency": 0,
        "skipped_oversize": 0
      },
      "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/flutterwave/qDeolzDYuBmJM9Pvv_2jgDghqSE1rmu8UguT_uvWcmY",
      "created_at": "2026-10-21T13:07:00.320075Z"
    },
    "summary": {
      "imported": 1,
      "duplicates": 0,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0,
      "fees": 1,
      "refunds": 0,
      "fetched": 2
    }
  },
  "meta": null
}
```

The ledger after the first sync: one charge as income (500000 kobo, category `Revenue`) and one fee
as expense (7500 kobo, category `Payment fees`), both `"source": "paystack"`. All amounts are integer
**kobo** (minor units). `capture: transactions_list_all_after_paystack_sync.json`

```json
{
  "data": {
    "transactions": [
      {
        "id": "1035851e-65a3-4a53-906e-88f60285e308",
        "date": "2026-10-01",
        "description": "Paystack fee for stub_ps_charge_1",
        "category": "Payment fees",
        "amount_minor": 7500,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.752026Z"
      },
      {
        "id": "2f45d406-fc1c-450d-9c8c-430d5f15c287",
        "date": "2026-10-01",
        "description": "Paystack payment stub_ps_charge_1",
        "category": "Revenue",
        "amount_minor": 500000,
        "currency": "NGN",
        "direction": "in",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.752026Z"
      }
    ]
  },
  "meta": null
}
```

Flutterwave amounts arrive in naira and are converted by the server: the stub's `5000` NGN charge
became 500000 kobo and the `75.5` fee became 7550 kobo. `capture: transactions_list_after_flutterwave_sync.json`

```json
{
  "data": {
    "transactions": [
      {
        "id": "160d987f-8230-46b5-bab3-770528ccffc5",
        "date": "2026-10-21",
        "description": "Paystack fee INV-2026-001-bfc32f9c",
        "category": "Payment fees",
        "amount_minor": 2420,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
        "date": "2026-10-21",
        "description": "Invoice INV-2026-001: Acme Traders Ltd",
        "category": "Revenue",
        "amount_minor": 100000,
        "currency": "NGN",
        "direction": "in",
        "source": "invoice",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "e4bd4d2f-46f8-4407-901e-4b98fbb65ce8",
        "date": "2026-10-01",
        "description": "Flutterwave fee for stub_flw_charge_1",
        "category": "Payment fees",
        "amount_minor": 7550,
        "currency": "NGN",
        "direction": "out",
        "source": "flutterwave",
        "created_at": "2026-10-21T13:07:00.358821Z",
        "updated_at": "2026-10-21T13:07:00.358821Z"
      },
      {
        "id": "1c7f0fdd-890c-4640-aedc-a037f91ffdd8",
        "date": "2026-10-01",
        "description": "Flutterwave payment stub_flw_charge_1",
        "category": "Revenue",
        "amount_minor": 500000,
        "currency": "NGN",
        "direction": "in",
        "source": "flutterwave",
        "created_at": "2026-10-21T13:07:00.358821Z",
        "updated_at": "2026-10-21T13:07:00.358821Z"
      },
      {
        "id": "1035851e-65a3-4a53-906e-88f60285e308",
        "date": "2026-10-01",
        "description": "Paystack fee for stub_ps_charge_1",
        "category": "Payment fees",
        "amount_minor": 7500,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.752026Z"
      },
      {
        "id": "2f45d406-fc1c-450d-9c8c-430d5f15c287",
        "date": "2026-10-01",
        "description": "Paystack payment stub_ps_charge_1",
        "category": "Sales",
        "amount_minor": 500000,
        "currency": "NGN",
        "direction": "in",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.824096Z"
      }
    ]
  },
  "meta": null
}
```

### Sync errors

| Case | Status | `code` | `message` | Capture |
|---|---|---|---|---|
| mentor | 403 | `FORBIDDEN` | `You don't have permission to do that.` | `error_sync_mentor_forbidden.json` |
| Connection is `needs_attention` | 404 | `NOT_FOUND` | `Not found.` | `error_sync_needs_attention.json` |
| Connection is `disconnected` or never connected | 404 | `NOT_FOUND` | `Not found.` | `error_sync_disconnected.json` |
| Synced very recently (default window 30 s) | 429 | `SYNC_TOO_SOON` | `Synced a moment ago. Try again shortly.` | ⚠️ **not verified live**: the e2e run sets the window to 0 so it can sync twice. Text read from `sync.py`; unit-tested |
| Another sync of this connection is running | 409 | `SYNC_IN_PROGRESS` | `A sync is already running. Try again shortly.` | ⚠️ **not verified live** (needs concurrency). Text read from the route; unit-tested |

`capture: error_sync_needs_attention.json`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**Sync UX.** Disable the Sync button while the request is in flight (this avoids most
`SYNC_IN_PROGRESS`). On `SYNC_TOO_SOON` and `SYNC_IN_PROGRESS` show the `message` as a neutral info
toast, not an error, and re-enable after about 30 seconds. Background sync also runs hourly per
active connection ⚠️ (scheduler is unit-tested only), so `last_sync_at` can change without the user
acting. A reasonable cadence is: refresh `GET /integrations` when the page opens and after Sync; do
not poll. Show `last_sync_at` as "Last synced 14:02". After sync, refetch the transactions list.

**Summary counts to surface.** If `skipped_currency` is above 0, show "N non-NGN payments were not
imported (only NGN is supported)". If `skipped_oversize` is above 0, show "N payments were too large
to import". Both were `0` in the live run; ⚠️ non-zero values are unit-tested, not captured.
`invoice_matched` above 0 means a charge paid one of our invoices (it marks the invoice paid instead
of adding a second income row).

## 4. Imported rows: read-only except `category`

Changing the category works. `capture: transaction_imported_category_patched.json`

```json
{
  "data": {
    "id": "2f45d406-fc1c-450d-9c8c-430d5f15c287",
    "date": "2026-10-01",
    "description": "Paystack payment stub_ps_charge_1",
    "category": "Sales",
    "amount_minor": 500000,
    "currency": "NGN",
    "direction": "in",
    "source": "paystack",
    "created_at": "2026-10-21T13:06:58.752026Z",
    "updated_at": "2026-10-21T13:06:58.824096Z"
  },
  "meta": null
}
```

Any other field or DELETE is 422. Note the `field` is `source` and the message is the same for both.
`capture: error_patch_imported_amount.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Imported transactions are read-only except the category.",
    "field_errors": [
      {
        "field": "source",
        "message": "Imported transactions are read-only except the category."
      }
    ]
  }
}
```

`capture: error_delete_imported_transaction.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Imported transactions are read-only except the category.",
    "field_errors": [
      {
        "field": "source",
        "message": "Imported transactions are read-only except the category."
      }
    ]
  }
}
```

## 5. Disconnect (founder only)

`POST /finance/integrations/{provider}/disconnect`, no body. Both stored secrets are wiped; imported
rows stay. `capture: disconnect_paystack.json`

```json
{
  "data": {
    "id": "419732d3-6f58-48da-a468-a939691b8487",
    "provider": "paystack",
    "status": "disconnected",
    "mode": "test",
    "key_last4": "10e0",
    "account_name": null,
    "last_sync_at": "2026-10-21T13:06:58.795565Z",
    "last_error": null,
    "last_sync_summary": {
      "fees": 0,
      "fetched": 2,
      "refunds": 0,
      "imported": 0,
      "duplicates": 2,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0
    },
    "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
    "created_at": "2026-10-21T13:06:58.716100Z"
  },
  "meta": null
}
```

Ledger afterwards still holds the imported rows. `capture: transactions_list_after_disconnect.json`

```json
{
  "data": {
    "transactions": [
      {
        "id": "160d987f-8230-46b5-bab3-770528ccffc5",
        "date": "2026-10-21",
        "description": "Paystack fee INV-2026-001-bfc32f9c",
        "category": "Payment fees",
        "amount_minor": 2420,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
        "date": "2026-10-21",
        "description": "Invoice INV-2026-001: Acme Traders Ltd",
        "category": "Revenue",
        "amount_minor": 100000,
        "currency": "NGN",
        "direction": "in",
        "source": "invoice",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "1035851e-65a3-4a53-906e-88f60285e308",
        "date": "2026-10-01",
        "description": "Paystack fee for stub_ps_charge_1",
        "category": "Payment fees",
        "amount_minor": 7500,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.752026Z"
      },
      {
        "id": "2f45d406-fc1c-450d-9c8c-430d5f15c287",
        "date": "2026-10-01",
        "description": "Paystack payment stub_ps_charge_1",
        "category": "Sales",
        "amount_minor": 500000,
        "currency": "NGN",
        "direction": "in",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.824096Z"
      }
    ]
  },
  "meta": null
}
```

After disconnect `GET /integrations` shows `connection: null` for both providers
(`capture: integrations_after_disconnect.json`), the old webhook URL answers 404
(`capture: error_webhook_disconnected_connection.json`), and sync is 404. Disconnect by an
accountant: 403 (`capture: error_disconnect_accountant_forbidden.json`).

```json
{
  "data": {
    "encryption_configured": true,
    "providers": [
      {
        "provider": "paystack",
        "label": "Paystack",
        "key_hint": "Starts with sk_test_ or sk_live_",
        "connection": null
      },
      {
        "provider": "flutterwave",
        "label": "Flutterwave",
        "key_hint": "Starts with FLWSECK_TEST- or FLWSECK-",
        "connection": null
      }
    ]
  },
  "meta": null
}
```

---

## State machine and what the FE does in each state

| `status` | Meaning | Card shows | Actions |
|---|---|---|---|
| (none) | `connection` is `null` | Connect form for each provider | Connect (founder) |
| `active` | working | name, mode badge, `••••last4`, last synced, webhook URL | Sync (all finance roles), Replace key, Disconnect (founder) |
| `needs_attention` | the provider rejected the saved key during a sync; syncing is paused | **Warning banner** with `last_error` | Replace key (founder). Sync returns 404, and incoming webhooks are ignored (404) until the key is replaced, so tell the founder invoices will not auto-mark as paid meanwhile (unit-tested, not captured live) |
| `disconnected` | founder disconnected | looks like "none" (the API returns `connection: null`) | Connect again |

`needs_attention` set by the real sync worker on a provider auth failure, with
`last_error` = `The provider rejected the saved key. Replace it to resume syncing.` In the live run
the stub never revokes a key, so the journey **forced** this status with a direct database update
to capture the read shape. The GET after forcing it: `capture: integrations_needs_attention_forced.json`

```json
{
  "data": {
    "encryption_configured": true,
    "providers": [
      {
        "provider": "paystack",
        "label": "Paystack",
        "key_hint": "Starts with sk_test_ or sk_live_",
        "connection": {
          "id": "419732d3-6f58-48da-a468-a939691b8487",
          "provider": "paystack",
          "status": "needs_attention",
          "mode": "test",
          "key_last4": "eac6",
          "account_name": null,
          "last_sync_at": "2026-10-21T13:06:58.795565Z",
          "last_error": "The provider rejected the saved key. Replace it to resume syncing.",
          "last_sync_summary": {
            "fees": 0,
            "fetched": 2,
            "refunds": 0,
            "imported": 0,
            "duplicates": 2,
            "invoice_matched": 0,
            "skipped_currency": 0,
            "skipped_oversize": 0
          },
          "webhook_url": "http://127.0.0.1:8041/api/v1/webhooks/payments/paystack/Jm6llDFqfPbiTDoy7-6udv5updcQwqBuQ2brSwANtps",
          "created_at": "2026-10-21T13:06:58.716100Z"
        }
      },
      {
        "provider": "flutterwave",
        "label": "Flutterwave",
        "key_hint": "Starts with FLWSECK_TEST- or FLWSECK-",
        "connection": null
      }
    ]
  },
  "meta": null
}
```

⚠️ The transition itself (real provider revokes key, sync flips to `needs_attention`) is unit-tested,
not verified live. While `needs_attention`, the webhook route still accepts signed events for the
connection ⚠️ (code and unit test, not captured). A temporary provider outage does **not** change
the status; only `last_error` is set and the status stays `active`. Show `last_error` as a small
warning line even when `active`.

---

## Invoice pay links

Sending an NGN invoice while a connection is `active` creates the link. The invoice object gained
three fields. `capture: invoice_sent_with_pay_link.json`

```json
{
  "data": {
    "id": "ccf760dc-4b4f-4807-affb-c995df34303d",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+pay-da3a1aced2c5@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c",
    "pay_provider": "paystack",
    "paid_via": null,
    "created_at": "2026-10-21T13:06:58.857359Z",
    "updated_at": "2026-10-21T13:06:58.883725Z"
  },
  "meta": null
}
```

| New field | Type | Notes |
|---|---|---|
| `pay_url` | string \| null | the hosted checkout link; `null` when none was created |
| `pay_provider` | `"paystack"` \| `"flutterwave"` \| null | which provider issued the link |
| `paid_via` | `"paystack"` \| `"flutterwave"` \| null | set only when the provider's webhook paid the invoice |

All three are present on **every** invoice (null when unused). Failing to create a link never fails
`send`: the invoice is `sent` with `pay_url: null`. The client email contains a **Pay now** button
to the same link. `capture: invoice_email_with_pay_link.json` (read from the file mailbox, not real
Resend)

```json
{
  "to": "delivered+pay-da3a1aced2c5@resend.dev",
  "subject": "Invoice INV-2026-001 from Payments Co",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><h2>Invoice INV-2026-001</h2><p>From Payments Co</p><p>Bill to Acme Traders Ltd</p><table style='width:100%'><tr><td>Retainer (x1)</td><td style='text-align:right'>1,000.00 NGN</td></tr></table><p>Subtotal: 1,000.00 NGN<br>Tax: 0.00 NGN<br><strong>Total: 1,000.00 NGN</strong></p><p>Due: 2026-11-20</p><p><a href=\"https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c\" style='display:inline-block;padding:10px 18px;background:#0b6b3a;color:#fff;text-decoration:none;border-radius:6px'>Pay now</a></p></div>",
  "sent_at": "2026-10-21T13:06:59.937352+00:00"
}
```

### `POST /finance/invoices/{id}/pay-link`

No body. Creates the link if missing, otherwise returns the existing one (same URL, no second link).
`capture: pay_link_reused.json`

```json
{
  "data": {
    "id": "ccf760dc-4b4f-4807-affb-c995df34303d",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+pay-da3a1aced2c5@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c",
    "pay_provider": "paystack",
    "paid_via": null,
    "created_at": "2026-10-21T13:06:58.857359Z",
    "updated_at": "2026-10-21T13:06:58.883725Z"
  },
  "meta": null
}
```

Retry after connecting a provider on an invoice sent earlier without one (Flutterwave here).
`capture: pay_link_retry_flutterwave.json`

```json
{
  "data": {
    "id": "c35ee790-582d-4f7d-b104-f8b064520bf9",
    "number": "INV-2026-003",
    "client_name": "Gamma Ltd",
    "client_email": "delivered+pay-3269ebfdf2c2@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": "https://checkout.flutterwave.com/v3/hosted/pay/stub-INV-2026-003-ac557e80",
    "pay_provider": "flutterwave",
    "paid_via": null,
    "created_at": "2026-10-21T13:07:00.272661Z",
    "updated_at": "2026-10-21T13:07:00.342092Z"
  },
  "meta": null
}
```

| Case | Status | `code` | Exact message | Capture |
|---|---|---|---|---|
| Invoice is a draft | 422 | `VALIDATION_ERROR` | field `status`: `Only a sent invoice can have a pay link.` | `error_pay_link_on_draft.json` |
| No active connection | 422 | `NO_PAYMENT_CONNECTION` | `Connect Paystack or Flutterwave before creating a pay link.` | `error_pay_link_no_connection.json` |
| mentor | 403 | `FORBIDDEN` | `You don't have permission to do that.` | `error_pay_link_mentor_forbidden.json` |
| Non-NGN invoice | 422 | `VALIDATION_ERROR` | `Pay links are only available for NGN invoices.` | ⚠️ not verified live (message from `finance.py`) |
| Provider could not create the link | 502 | `PAYMENT_PROVIDER_UNAVAILABLE` | `We could not create a pay link right now. Try again in a few minutes.` | ⚠️ not verified live (message from `finance.py`) |

`capture: error_pay_link_no_connection.json`

```json
{
  "error": {
    "code": "NO_PAYMENT_CONNECTION",
    "message": "Connect Paystack or Flutterwave before creating a pay link.",
    "field_errors": []
  }
}
```

`capture: error_pay_link_on_draft.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a sent invoice can have a pay link."
      }
    ]
  }
}
```

A sent invoice created while nothing is connected: `capture: invoice_sent_without_connection.json`

```json
{
  "data": {
    "id": "c35ee790-582d-4f7d-b104-f8b064520bf9",
    "number": "INV-2026-003",
    "client_name": "Gamma Ltd",
    "client_email": "delivered+pay-3269ebfdf2c2@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "created_at": "2026-10-21T13:07:00.272661Z",
    "updated_at": "2026-10-21T13:07:00.285161Z"
  },
  "meta": null
}
```

**Pay-link UX.** On a sent invoice show **Copy link** when `pay_url` is set. When `pay_url` is `null`
and the invoice is `sent` and NGN: if a connection is `active`, show **Create pay link** (retry);
otherwise show "Connect Paystack or Flutterwave to accept payments" linking to Integrations.
After a retry, refresh the invoice from the response.

### Automatic payment (webhook)

When the client pays, the provider calls the webhook, the server verifies the signature, and
the invoice becomes `paid`, `paid_via` is set, and one income row is booked (`source: "invoice"`).
The provider fee, if sent, is booked as a `Payment fees` expense. The FE sees this only by
refetching, so refetch the invoice list when the Invoices screen gains focus, and poll an open
invoice every 10 to 15 seconds only while a payment page is expected to be in use.

Paystack, valid signature. The test sends a `charge.success` event whose `reference` is the invoice's
pay reference, signed with the key it connected. Response `capture: webhook_paystack_charge_accepted.json`

```json
{
  "data": {
    "received": true
  },
  "meta": null
}
```

The same event delivered again is acknowledged and does nothing. `capture: webhook_paystack_charge_duplicate.json`

```json
{
  "data": {
    "received": true,
    "duplicate": true
  },
  "meta": null
}
```

The invoice afterwards. `capture: invoice_paid_via_paystack.json`

```json
{
  "data": {
    "id": "ccf760dc-4b4f-4807-affb-c995df34303d",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+pay-da3a1aced2c5@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": "2026-10-21T13:06:59Z",
    "auto_remind": false,
    "transaction_id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c",
    "pay_provider": "paystack",
    "paid_via": "paystack",
    "created_at": "2026-10-21T13:06:58.857359Z",
    "updated_at": "2026-10-21T13:07:00.012653Z"
  },
  "meta": null
}
```

Show a **Paid via Paystack** tag from `paid_via` (and "Paid via Flutterwave"). The Mark paid and
Mark unpaid buttons are not shown for provider-paid invoices. A manual Mark paid on it is a harmless
no-op (200, same `transaction_id`). `capture: invoice_mark_paid_after_webhook_noop.json`

```json
{
  "data": {
    "id": "ccf760dc-4b4f-4807-affb-c995df34303d",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+pay-da3a1aced2c5@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": "2026-10-21T13:06:59Z",
    "auto_remind": false,
    "transaction_id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c",
    "pay_provider": "paystack",
    "paid_via": "paystack",
    "created_at": "2026-10-21T13:06:58.857359Z",
    "updated_at": "2026-10-21T13:07:00.012653Z"
  },
  "meta": null
}
```

Mark unpaid is refused. `capture: error_mark_unpaid_provider_paid.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This invoice was paid through Paystack and cannot be marked unpaid.",
    "field_errors": [
      {
        "field": "status",
        "message": "This invoice was paid through Paystack and cannot be marked unpaid."
      }
    ]
  }
}
```

The Flutterwave variant has the same shape with its own provider name. `capture: error_mark_unpaid_provider_paid_flutterwave.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This invoice was paid through Flutterwave and cannot be marked unpaid.",
    "field_errors": [
      {
        "field": "status",
        "message": "This invoice was paid through Flutterwave and cannot be marked unpaid."
      }
    ]
  }
}
```

The ledger: exactly one `source: "invoice"` income row, no second row from the provider.
`capture: transactions_list_inflows_after_invoice_paid.json`

```json
{
  "data": {
    "transactions": [
      {
        "id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
        "date": "2026-10-21",
        "description": "Invoice INV-2026-001: Acme Traders Ltd",
        "category": "Revenue",
        "amount_minor": 100000,
        "currency": "NGN",
        "direction": "in",
        "source": "invoice",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "2f45d406-fc1c-450d-9c8c-430d5f15c287",
        "date": "2026-10-01",
        "description": "Paystack payment stub_ps_charge_1",
        "category": "Sales",
        "amount_minor": 500000,
        "currency": "NGN",
        "direction": "in",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.824096Z"
      }
    ]
  },
  "meta": null
}
```

The fee row from the webhook (2420 kobo, `Payment fees`) is in `capture: transactions_list_after_webhook.json`

```json
{
  "data": {
    "transactions": [
      {
        "id": "160d987f-8230-46b5-bab3-770528ccffc5",
        "date": "2026-10-21",
        "description": "Paystack fee INV-2026-001-bfc32f9c",
        "category": "Payment fees",
        "amount_minor": 2420,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
        "date": "2026-10-21",
        "description": "Invoice INV-2026-001: Acme Traders Ltd",
        "category": "Revenue",
        "amount_minor": 100000,
        "currency": "NGN",
        "direction": "in",
        "source": "invoice",
        "created_at": "2026-10-21T13:07:00.012653Z",
        "updated_at": "2026-10-21T13:07:00.012653Z"
      },
      {
        "id": "1035851e-65a3-4a53-906e-88f60285e308",
        "date": "2026-10-01",
        "description": "Paystack fee for stub_ps_charge_1",
        "category": "Payment fees",
        "amount_minor": 7500,
        "currency": "NGN",
        "direction": "out",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.752026Z"
      },
      {
        "id": "2f45d406-fc1c-450d-9c8c-430d5f15c287",
        "date": "2026-10-01",
        "description": "Paystack payment stub_ps_charge_1",
        "category": "Sales",
        "amount_minor": 500000,
        "currency": "NGN",
        "direction": "in",
        "source": "paystack",
        "created_at": "2026-10-21T13:06:58.752026Z",
        "updated_at": "2026-10-21T13:06:58.824096Z"
      }
    ]
  },
  "meta": null
}
```

Flutterwave: same flow with the `verif-hash` header. `capture: webhook_flutterwave_charge_accepted.json`

```json
{
  "data": {
    "received": true
  },
  "meta": null
}
```

`capture: webhook_flutterwave_charge_duplicate.json`

```json
{
  "data": {
    "received": true,
    "duplicate": true
  },
  "meta": null
}
```

`capture: invoice_paid_via_flutterwave.json`

```json
{
  "data": {
    "id": "c35ee790-582d-4f7d-b104-f8b064520bf9",
    "number": "INV-2026-003",
    "client_name": "Gamma Ltd",
    "client_email": "delivered+pay-3269ebfdf2c2@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": "2026-10-21T13:07:00Z",
    "auto_remind": false,
    "transaction_id": "d2742c43-1bad-464d-944d-62e6d5555776",
    "pay_url": "https://checkout.flutterwave.com/v3/hosted/pay/stub-INV-2026-003-ac557e80",
    "pay_provider": "flutterwave",
    "paid_via": "flutterwave",
    "created_at": "2026-10-21T13:07:00.272661Z",
    "updated_at": "2026-10-21T13:07:00.411776Z"
  },
  "meta": null
}
```

### Webhook mismatch is silent to the FE

If a verified payment's amount or currency differs from the invoice total, the server still replies
200 `{"received": true}` (so the provider stops retrying) but **does not** mark the invoice paid. The
invoice stays `sent`. The response is indistinguishable from success. `capture: webhook_paystack_amount_mismatch_acked.json`

```json
{
  "data": {
    "received": true
  },
  "meta": null
}
```

`capture: invoice_still_sent_after_mismatch.json`

```json
{
  "data": {
    "id": "b6314b5f-9477-482a-b225-bb7058f530fc",
    "number": "INV-2026-002",
    "client_name": "Beta Foods",
    "client_email": "delivered+pay-7de56b02ce3d@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 100000,
        "amount_minor": 200000
      }
    ],
    "subtotal_minor": 200000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 200000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-002-9191eb00",
    "pay_provider": "paystack",
    "paid_via": null,
    "created_at": "2026-10-21T13:07:00.113672Z",
    "updated_at": "2026-10-21T13:07:00.129695Z"
  },
  "meta": null
}
```

**UX consequence.** A client who underpays through the link will not flip the invoice to paid; the
user must reconcile it by hand (they will see the payment on the provider side, and it is not
added to the ledger as invoice income). Tell users in help text: "Invoices are marked paid only when
the amount matches exactly."

### Webhook errors (for support and debugging only, the FE never calls this route)

| Case | Status | `code` | `message` | Capture |
|---|---|---|---|---|
| Wrong key signature (Paystack) | 401 | `INVALID_SIGNATURE` | `Invalid signature.` | `error_webhook_wrong_signature.json` |
| Missing signature header | 401 | `INVALID_SIGNATURE` | `Invalid signature.` | `error_webhook_missing_signature.json` |
| Body changed after signing | 401 | `INVALID_SIGNATURE` | `Invalid signature.` | `error_webhook_tampered_body.json` |
| Wrong `verif-hash` (Flutterwave) | 401 | `INVALID_SIGNATURE` | `Invalid signature.` | `error_webhook_flutterwave_wrong_hash.json` |
| Empty `verif-hash` | 401 | `INVALID_SIGNATURE` | `Invalid signature.` | `error_webhook_flutterwave_empty_hash.json` |
| Unknown connection id | 404 | `NOT_FOUND` | `Not found.` | `error_webhook_unknown_connection.json` |
| Disconnected connection | 404 | `NOT_FOUND` | `Not found.` | `error_webhook_disconnected_connection.json` |
| Body over 256 KB | 413 | `PAYLOAD_TOO_LARGE` | `The request body is too large.` | ⚠️ not verified live (route source) |
| Signed but unreadable JSON | 400 | `INVALID_PAYLOAD` | `The webhook body could not be read.` | ⚠️ not verified live (source) |
| More than 120 requests a minute from one IP | 429 | not captured | not captured | ⚠️ **not verified**: the e2e run raises the rate limit to 100000 a minute |

`capture: error_webhook_wrong_signature.json`

```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Invalid signature.",
    "field_errors": []
  }
}
```

`capture: error_webhook_unknown_connection.json`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

---

## UX instructions (explicit)

1. **Key never shown again.** After a successful connect the key input is cleared and unmounted. Show
   `••••<key_last4>`. Say so: "For your security we never show your key again."
2. **Test mode badge.** If `mode == "test"` show a visible **Test mode** badge on the card and on
   pay links. Money in test mode is not real.
3. **Replace needs re-entry.** The Replace action opens the same form with empty fields; a Flutterwave
   replace needs the secret hash typed again too.
4. **Flutterwave secret hash.** For Flutterwave render a second field labelled "Secret hash" with
   help text: "The value you set under Settings, Webhooks in your Flutterwave dashboard."
5. **Webhook URL.** Show `webhook_url` with **Copy** and the instruction "Paste this URL into your
   {Paystack|Flutterwave} dashboard under Webhooks so paid invoices update automatically."
6. **`needs_attention` banner.** When `status == "needs_attention"` show a prominent banner with
   `last_error` and a Replace key button. Syncing and auto-sync are paused until then. (There is no
   push notification for this yet.)
7. **Paid via tag.** Invoice with `paid_via` set: "Paid via Paystack" or "Paid via Flutterwave";
   hide Mark unpaid.
8. **Copy link.** A Copy link button on sent invoices with `pay_url`.
9. **Pay-link retry.** Create pay link button when `pay_url` is `null` on a sent NGN invoice and a
   connection is `active`; handle `NO_PAYMENT_CONNECTION` and the 502 inline.
10. **Sync toasts.** `SYNC_TOO_SOON` and `SYNC_IN_PROGRESS`: show the `message`, no red error.
11. **Skipped counts.** Surface `skipped_currency` and `skipped_oversize` after a sync when above 0.
12. **Imported rows.** Category-only editing; no delete. Label the source ("Paystack").
13. **Roles.** Hide Connect, Replace and Disconnect unless the user is a founder; team members and
    accountants see status and Sync. Mentors and other roles do not see Integrations at all.
14. **No em or en dashes** in any text you add near these screens (product copy rule).

---

## Verification table

Legend: **LIVE (stub)** = asserted in `e2e/test_finance_payments.py` against the running server with
the stubbed provider HTTP, body captured. **NOT VERIFIED** = not exercised against the real provider
or not exercised at all in e2e; the source of the shape is named.

| Behaviour | Status | Evidence / source |
|---|---|---|
| GET integrations empty / connected; both providers always listed | LIVE (stub) | `integrations_empty.json`, `integrations_paystack_connected.json` |
| 403 for mentor on GET, connect, sync, pay-link; 401 unauthenticated | LIVE | `error_integrations_*`, `error_*_mentor_forbidden.json` |
| 403 for team_member and accountant on connect, accountant on disconnect | LIVE | `error_connect_*_forbidden.json`, `error_disconnect_accountant_forbidden.json` |
| Accountant and team_member can GET; accountant can sync | LIVE (statuses asserted, bodies not captured) | journey |
| Connect Paystack and Flutterwave (hash), mode `test`, `key_last4`, no secret in any body | LIVE (stub) | `connect_*.json`, every capture asserted free of the sent secrets |
| Connect validation 422s, `PAYMENT_KEY_REJECTED`, 409 other provider, 404 unknown provider | LIVE (stub: "bad" in key means rejected) | `error_connect_*.json` |
| Replace keeps `id` and `webhook_url`, clears error | LIVE (after a forced `needs_attention`) | `connect_paystack_replaced.json` |
| Reconnect after disconnect reuses the row with a new `webhook_url` token | NOT VERIFIED live | `connections.py`, unit test |
| `needs_attention` set by a real provider key revocation during sync | NOT VERIFIED live (status forced via DB to capture the read shape) | `integrations_needs_attention_forced.json`; unit test for the transition |
| Sync imports charge and fee; second sync imports 0 and counts duplicates | LIVE (stub) | `sync_paystack_*.json`, `sync_flutterwave.json` |
| Ledger rows `source` paystack / flutterwave, amounts in kobo, Flutterwave naira converted | LIVE (stub fixtures) | `transactions_list_*.json` |
| Imported rows: category patch OK, other field and DELETE 422 | LIVE | `transaction_imported_category_patched.json`, `error_*imported*` |
| `skipped_currency`, `skipped_oversize`, refunds non-zero | NOT VERIFIED live (all zero in e2e) | unit tests |
| Refund payloads from the real providers | NOT VERIFIED | handwritten fixtures only; no refund in the stub listing |
| `SYNC_TOO_SOON` (429) and `SYNC_IN_PROGRESS` (409) | NOT VERIFIED live | e2e sets the sync window to 0; text from source; unit tests |
| Hourly background sync | NOT VERIFIED live | scheduler unit tests |
| Pay link created on send; email carries it; reuse; retry after connect; draft and no-connection 422s | LIVE (stub link) | `invoice_sent_with_pay_link.json`, `invoice_email_with_pay_link.json`, `pay_link_*.json`, `error_pay_link_*.json` |
| Non-NGN 422 and provider-failure 502 on pay-link | NOT VERIFIED live | source and unit tests |
| Flutterwave payment-link request (amount sent as a string) accepted by the real API | NOT VERIFIED | the stub accepts any body; check against the real Flutterwave `/v3/payments` |
| Real Paystack and Flutterwave response shapes (key check, listing, balances, link creation) | NOT VERIFIED | handwritten fixtures under `tests/fixtures/payments/` and the stub |
| Real webhook delivery to our URL, real signature headers (`x-paystack-signature`, `verif-hash`), retry behaviour | NOT VERIFIED | the test computes signatures itself with our own implementation of the documented scheme; a mistake in the scheme would be invisible here |
| Webhook marks invoice paid once, duplicate acked with `duplicate: true`, both providers | LIVE (self-signed) | `webhook_*_accepted.json`, `webhook_*_duplicate.json`, `invoice_paid_via_*.json` |
| Concurrent duplicate webhook delivery | NOT VERIFIED live | unit test |
| Webhook 401s: wrong key, missing, tampered, wrong or empty `verif-hash`; 404 unknown / disconnected | LIVE | `error_webhook_*.json` |
| Non-ASCII signature header, 413 oversize body, 400 bad JSON | NOT VERIFIED live | unit tests / source |
| Amount mismatch acked, invoice stays `sent` | LIVE | `webhook_paystack_amount_mismatch_acked.json`, `invoice_still_sent_after_mismatch.json` |
| Mark-unpaid on provider-paid invoice 422 (both providers); manual mark-paid no-op | LIVE | `error_mark_unpaid_provider_paid*.json`, `invoice_mark_paid_after_webhook_noop.json` |
| Disconnect keeps ledger, wipes access, old webhook URL 404, sync 404 | LIVE | `disconnect_paystack.json`, `transactions_list_after_disconnect.json`, `error_*disconnected*.json` |
| Webhook rate limit of 120 requests a minute per IP | NOT VERIFIED | e2e raises the limit to 100000 a minute |
| `webhook_url` host in staging and production | NOT VERIFIED | depends on the deployment's `API_PUBLIC_URL`; e2e value is `http://127.0.0.1:8041` |
| `account_name` populated from a real provider | NOT VERIFIED | stub returns none |
| 503 `FINANCE_CONNECTIONS_NOT_CONFIGURED` and `encryption_configured: false` | NOT VERIFIED live | unit tests |
