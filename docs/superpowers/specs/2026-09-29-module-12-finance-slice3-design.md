# Module 12 — Finance Hub, Slice 3: Invoices — Design

**Status:** Draft for review
**Date:** 2026-09-29
**Depends on:** Slice 1 (`Transaction` model + cash-flow), Slice 2 (runway — the paid-invoice inflow flows into it automatically).
**Spec author note:** every payload shape here is a *target*; the FE integration guide is re-derived verbatim from live e2e captures at build time.

---

## 1. Goal

Back the Finance → **Invoices** screen: create invoices from a line-item builder, send them to a client by email (Resend), track their status (draft → sent → paid, overdue derived), and — the integrating decision — **have a paid invoice post an inflow transaction so it flows into cash-flow and runway**.

## 2. FE cross-check (`../cofoundaz/app/(dashboard)/finance/invoices/page.tsx` + `hooks/useFinanceApi.ts`)

| FE element | Backend obligation |
| --- | --- |
| List + status filter (All/Draft/Sent/Paid/Overdue) | `GET /finance/invoices?status=` returning rows with a **derived** `overdue` status |
| Columns: number, clientName, dueOn, totalMinor, autoRemind, status | `Invoice` fields; `number` server-generated `INV-YYYY-NNN` |
| Builder: client, line items (desc/qty/price), tax %, terms | line items stored as JSONB; **server computes** subtotal/tax/total (never trust FE totals) |
| "Send Invoice" | `POST /finance/invoices/{id}/send` → draft→sent, set `issued_on`/`due_on`, email the client via Resend |
| "Mark paid" | `POST /finance/invoices/{id}/mark-paid` → sent→paid, **create an idempotent inflow transaction** |
| Auto-remind checkbox | `auto_remind` bool stored + toggled (the reminder *scheduler* is deferred — §9) |
| "Download PDF" | **Deferred** this slice (no server-side PDF); the button stays FE-only for now |

FE `Invoice` shape: `{ number, clientName, totalMinor, status: draft|sent|paid|overdue, issuedOn, dueOn, autoRemind }`. Client is **free-text** (`clientName` + a `client_email` we add for sending) — no separate CRM/Client entity this slice.

**FE-guide traps to flag:** money in minor units; `overdue` is a *derived* read-time status, never stored (the FE must not PATCH it); totals are server-computed (the builder's numbers are advisory).

## 3. Data model

**`invoices`** — one row per invoice, per startup.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | |
| `startup_id` | UUID FK→startups CASCADE, index | tenancy |
| `number` | String(20) | `INV-YYYY-NNN`, unique per `(startup_id, number)` |
| `client_name` | String(200) | free text |
| `client_email` | String(320) | required to send; validated as email |
| `line_items` | JSONB | `[{description, quantity, unit_price_minor}]` (caps: ≤50 items, strings ≤300) |
| `subtotal_minor` | Integer | server-computed Σ(qty×unit_price_minor), int32-bounded |
| `tax_percent` | Numeric(5,2) | 0…100 |
| `tax_minor` | Integer | server-computed round(subtotal×tax%/100) |
| `total_minor` | Integer | server-computed subtotal+tax, int32-bounded |
| `currency` | String(3) | default NGN |
| `terms` | Enum(InvoiceTerms) | `net_15` / `net_30` / `due_on_receipt` |
| `status` | Enum(InvoiceStatus) | stored: `draft` / `sent` / `paid` (never `overdue`) |
| `issued_on` | Date, nullable | set on send |
| `due_on` | Date, nullable | set on send = issued_on + terms-days |
| `paid_at` | timestamptz, nullable | set on mark-paid |
| `auto_remind` | Boolean, default False | |
| `transaction_id` | UUID FK→transactions SET NULL, nullable, unique | the inflow created on paid (idempotency + reversal link) |
| `created_at` / `updated_at` | timestamptz | |

`InvoiceStatus` (stored) = `draft|sent|paid`. **`overdue` is derived on read**: `status==sent AND due_on < today`. `InvoiceTerms` = `net_15|net_30|due_on_receipt` (due-days 15/30/0). Both `Enum(native_enum=False, values_callable=...)` per house style.

Migration **`0041_finance_invoices`**, `down_revision=0040_finance_runway`. **Migration-number watch:** Victoria's validation PR #103 now uses `0041_validation` (pushed, not merged). Whichever merges to develop first bumps the other to `0042`. Re-check develop head before the final push; robust single-head test.

**Enum reuse (no migration):** add `invoice` to `TransactionSource` (varchar-backed `Enum(native_enum=False, length=12)`; `"invoice"` is 7 chars — no DDL). The paid-invoice inflow uses `source=invoice` for provenance.

## 4. Number generation

`INV-{year}-{NNN}` sequential per startup per year. On create: `seq = 1 + count(invoices WHERE startup_id=? AND number LIKE 'INV-{year}-%')`, format `NNN` zero-padded (≥3 digits, grows past 999). Race-safety: `unique(startup_id, number)` + insert inside `begin_nested()`; on `IntegrityError`, recompute seq and retry (bounded retries) — the SAVEPOINT pattern from Slice 1/2.

## 5. Status lifecycle & transitions

```
draft ──POST /send──▶ sent ──POST /mark-paid──▶ paid
  │                     │
  └── PATCH (edit)      └── (overdue derived when due_on < today)
```
- **send** (draft→sent only): requires `client_email` and ≥1 line item; sets `issued_on=today`, `due_on=issued_on+terms_days`, enqueues the client email; illegal from sent/paid → 422.
- **mark-paid** (sent→paid; also draft→paid allowed for a manually-recorded payment? — NO, keep sent→paid only for a clean state machine): sets `paid_at`, creates the inflow transaction (§6). Idempotent: if already paid, no-op/return current (never double-post).
- **unpay / reversal** (paid→sent, optional): deletes the linked transaction and clears `paid_at`/`transaction_id`. Include a `POST /mark-unpaid` so the cash-flow effect is reversible (the reviewer flagged reversibility as important for money mutations).
- **edit** (PATCH): only in `draft` (recompute totals); editing a sent/paid invoice → 422.
- **delete**: only `draft` → 204/`{deleted:true}`; sent/paid cannot be deleted (audit trail).
- Illegal transitions raise `_validation` → 422 (before mutating the row), mirroring the marketing campaign lifecycle.

## 6. Cash-flow integration (the paid-invoice inflow)

On **mark-paid**, create a `Transaction` (Slice 1 model): `direction=inflow`, `amount_minor=total_minor`, `currency=invoice.currency`, `date=paid date (today)`, `description="Invoice {number} — {client_name}"`, `category="Revenue"`, `source=invoice`. Store its id on `invoice.transaction_id`.
- **Idempotency:** if `transaction_id` is already set, do not create a second (the mark-paid endpoint is safe to retry).
- **Reversal:** mark-unpaid deletes that transaction and nulls `transaction_id`, so cash-on-hand/runway revert exactly.
- **Consequence:** paid invoices now feed `cash_flow_summary` (inflow) and therefore runway. This is the intended composition of Slices 1–3. The FE guide must state that marking paid moves the cash-flow numbers.
- Reuse `finance_svc` transaction creation but bypass the `source=manual` force (that force is specific to the manual-entry endpoint; the invoice path sets `source=invoice` directly in the service).

## 7. Sending the client email (Resend)

`app/platform/email.py` already provides `get_email_sender().send(EmailMessage(to, subject, html))` with a `ResendEmailSender` backend (`EMAIL_BACKEND=resend`). Mirror the `email.notification` job pattern:
- On **send**, enqueue an async job `email.invoice_sent` with `{invoice_id}` (after flush+commit), so the HTTP request returns fast and delivery failure can't fail the state transition.
- Handler `handle_invoice_email(db, job)`: load the invoice, render an invoice HTML email (number, client, line-item table, subtotal/tax/total, due date, terms), `get_email_sender().send(EmailMessage(to=invoice.client_email, subject=f"Invoice {number} from {startup name}", html=...))`. Strip CR/LF from the subject (header-injection guard, as the notification handler does).
- Register in `app/worker/__main__.py` (handler import) + `register_handler`.
- **e2e:** `EMAIL_BACKEND=file` (or the e2e default) captures the sent email to disk so the journey can assert it; per memory `resend-e2e-recipient`, a real Resend send 422s `example.com` — use `client_email = delivered+<uniq>@resend.dev` in the live journey if Resend is exercised; otherwise assert via the file backend. State in the FE guide which path was verified.

## 8. Endpoints (all `/api/v1/finance`, gated by the Slice-1 `_finance` dep: founder, team_member, accountant)

| Method + path | Purpose |
| --- | --- |
| `POST /finance/invoices` | create a draft (server computes totals + number) |
| `GET /finance/invoices?status=` | list, newest first; `status` filter incl. derived `overdue` |
| `GET /finance/invoices/{id}` | one invoice (status derived) |
| `PATCH /finance/invoices/{id}` | edit a **draft** (recompute totals); toggle `auto_remind` (allowed in any status) |
| `POST /finance/invoices/{id}/send` | draft→sent + email client |
| `POST /finance/invoices/{id}/mark-paid` | sent→paid + inflow transaction |
| `POST /finance/invoices/{id}/mark-unpaid` | paid→sent + delete inflow transaction |
| `DELETE /finance/invoices/{id}` | delete a **draft** only |

Response `InvoiceResponse` includes the derived `status` (`overdue` when applicable), `line_items`, all money fields, `issued_on`/`due_on`/`paid_at`, `auto_remind`, and `transaction_id`. Validation: explicit-null on required PATCH fields → 422 (Slice-1 `model_validator` pattern); `amount`/`unit_price` int32-bounded → 422; `tax_percent` 0…100; `client_email` a valid email; line_items non-empty to send.

## 9. Deferred (later slices / follow-ups, tracked in checklist)

- **Reminder + overdue scheduler** — the nightly job that flips sent→overdue-notification and sends 7-day reminders for `auto_remind` invoices (this slice stores the flag + derives overdue on read only).
- **Server-side PDF** — "Download PDF" stays FE-only.
- **Client/CRM entity** — clients are free-text this slice.
- **Multi-currency** — one currency per invoice, not converted (as Slices 1–2).
- **Health-Score / events** — no `invoice.*` domain events this slice (could add `invoice.paid`/`invoice.overdue` later).

## 10. Files (indicative)

**Create:** `app/db/models/invoice.py`; `app/schemas/invoice.py`; `app/services/finance/invoices.py` (CRUD, number-gen, transitions, mark-paid inflow, mark-unpaid); `app/worker/handlers/invoice_email.py`; `alembic/versions/0041_finance_invoices.py`; tests (`tests/api/test_invoices.py`, `tests/db/test_invoice_model.py`, `tests/test_invoice_migration.py`, `tests/services/test_invoice_transitions.py`, `e2e/test_invoices.py` + captures); `docs/fe-integration-guide-finance-invoices.md`, `docs/sop/2026-09-29-finance-slice3.md`.
**Modify:** `app/db/models/enums.py` (`InvoiceStatus`, `InvoiceTerms`, `TransactionSource.invoice`); `app/api/v1/endpoints/finance.py` (8 routes); `app/worker/__main__.py` (register handler); `docs/checklist/PROJECT_CHECKLIST.md`.

## 11. Cross-cutting rules (carried)

Money int32-bounded at validation (422 not 500); services flush / endpoints commit; tenancy `filter_by(startup_id=...)`, startup_id from membership; SAVEPOINT for unique-number inserts; no import cycle; CodeQL test hygiene; migration id ≤32 chars, single head; **no AI attribution** in commits/PR. Email send is async + best-effort (never fails the transition); subject CR/LF-stripped.

## 12. Testing

- **Unit:** number generation (sequential, per-year, race retry); server-side total computation (subtotal/tax/total, int32 bounds → 422); status transitions (legal + illegal → 422); mark-paid creates exactly one inflow (idempotent), mark-unpaid reverses it; overdue derived correctly (sent + past due); RBAC (accountant allowed, others 403); cross-tenant 404; explicit-null PATCH → 422; delete only draft.
- **Cash-flow composition:** after mark-paid, `cash_flow_summary` cash-on-hand rises by `total_minor`; after mark-unpaid it reverts.
- **Email:** send enqueues `email.invoice_sent`; the handler renders + sends via the file/console backend (assert recipient=client_email, subject, total present).
- **e2e:** create → send (assert email captured) → mark-paid (assert cash-flow moved) → list filter → mark-unpaid. Commit captures.
- Local CI parity green before push (pytest ≥95%, alembic single-head + check, e2e, static).
