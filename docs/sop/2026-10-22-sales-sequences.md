# SOP — Sales Hub slice B: email sequences and outreach sending (Module 11)

| | |
|---|---|
| **Shipped** | Branch `feature/sales-sequences` → PR to `develop` (link in the PR; built, awaiting review and merge) |
| **Date** | 2026-10-22 |
| **Area** | Sales: `app/db/models/sales_sequences.py`, `app/platform/outreach.py`, `app/schemas/sales_sequences.py`, `app/services/sales/{sequences,sending,suppression,bounces,unsubscribe,tokens,outreach_render,drafts,draft_prompts}.py`, `app/worker/handlers/sales_outreach.py`, `app/api/v1/endpoints/sales_sequences.py` |
| **Migration** | `0068_sales_sequences` (6 new tables, additive), `down_revision = 0066_finance_invoice_automation`. **Re-chain before merge:** Finance slice (c) builds `0067` in parallel; whichever PR lands second sets its `down_revision` to the other and re-runs the round-trip test. |
| **Spec** | `docs/superpowers/specs/2026-10-21-sales-design.md` (FE repo): §4 sequences, §5 sending, D-SAL-1, D-SAL-8, §11 lead calls. Provider decided 2026-10-22 (below). |
| **FE guide** | `docs/fe-integration-guide-sales-sequences.md` (from `e2e/_captures/sales_b/`) |

## What shipped

Real outreach sequences. A member builds a sequence (1 to 10 steps: wait days, subject, body with merge tags),
enrolls leads and contacts, and a scheduler sends each step from **one cPanel mailbox over SMTP**; bounce mail
is read back over **IMAP**. Also: signed one-click unsubscribe, a workspace suppression list, send caps,
auto-pause on a bounce spike, "Write with AI" for a step, and a deal follow-up draft that can be sent once.

## Why

D-SAL-1: sequences must really send, from a mailbox that is **not** the Resend account carrying the app's
auth email, so outreach reputation can never block signup or password reset. Adebayo chose a cPanel mailbox
(2026-10-22). cPanel has no webhooks, so the spec's provider webhook became an IMAP bounce poller.

## How (decisions)

| Decision | Choice | Alternative rejected |
|---|---|---|
| Sender seam | `OutreachSender` protocol (`send(msg) -> id`), `SmtpOutreachSender` (stdlib `smtplib`, driven phase by phase; `ssl` 465 / `starttls` 587 / `none` for local only), `ConsoleOutreachSender` (dev). **Only a 5xx on RCPT (not 530/535/554) is a bad address** (`OutreachRejectedError`: suppress + counts toward auto-pause). MAIL FROM refusals, AUTH/TLS/connect errors and a non-250 final DATA reply (spam/policy/quota) are transient and never suppress a contact. **Exactly-once:** any failure once DATA has started (timeout, disconnect awaiting the final reply) is `OutreachAmbiguousError`, settled as `unknown` (counts toward caps, enrollment advances, never retried; a follow-up answers `503 SEND_UNCERTAIN` and stays claimed). Once DATA returns 250 a failing QUIT is ignored. | A vendor SDK; treating every 5xx as a bad address (a blocked mailbox would suppress innocent contacts) |
| **No double send** | Sending is **at-most-once**. Phase 1 (one short transaction): lock the enrollment `FOR UPDATE SKIP LOCKED`, re-check everything, insert a `pending` row in `sequence_sends` (`UNIQUE (enrollment_id, step_index)`), push `next_send_at` out by a 30-minute lease, commit. Phase 2: SMTP with no lock or transaction held, then mark `sent` and advance. A worker that dies between the phases leaves a `pending` row; after the lease it becomes `unknown` and the step is **skipped, never resent**. | Send inside the transaction (a crash after SMTP but before commit would resend); at-least-once (a duplicate to a prospect is worse than a missed email) |
| **Caps are exact** | Every reservation takes one `pg_advisory_xact_lock`, then **counts the ledger** (`sequence_sends`) and inserts, so concurrent workers serialize and cannot both see room. No counter exists to drift. Caps: **hourly 30 global to the mailbox** (`SALES_OUTREACH_HOURLY_LIMIT`), **100 per workspace per UTC day**, **1 per recipient per UTC day** across all sequences and follow-ups. Over the hourly cap the whole tick stops (stays due, oldest first); over a daily cap the step rolls to the next UTC midnight. | A Redis counter (second source of truth, can drift from what was actually reserved) |
| Send windows | The spec defines no time-of-day window, so "windows" = the hour/day cap windows above plus each step's `wait_days` after the previous *actual* send (step 1: after enrolment). Weekends/business hours are not enforced. | Inventing a timezone-aware business-hours rule nobody specified |
| Suppression at send time | Re-checked under the lock for every step **and again immediately before the SMTP call** (an unsubscribe/bounce/Stop that lands in between wins; the ledger row is settled `failed: suppressed`): an unsubscribe or bounce that lands after enrolment still wins. Enrolling a suppressed address is skipped with `suppressed`. Suppression is per workspace (lead call 2). | Checking only at enrolment |
| **Unsubscribe token** | `<send id>.<HMAC-SHA256>` (both base64url). HMAC key = HKDF-style `HMAC(SALES_OUTREACH_UNSUBSCRIBE_KEY or SECRET_KEY, "cofoundaz.sales.unsubscribe.v1")`. Verified with `hmac.compare_digest` **before any DB read**; a forged, truncated or foreign token is a 404 indistinguishable from an unknown id. Never expires. The id signed is the **send** row (not the enrollment), so follow-up emails carry a link too and it keeps working after an enrollment is deleted. Replaces the spec's stored `unsubscribe_token_hash`. | Random token stored hashed (needs a lookup to reject garbage, one more column) |
| Unsubscribe endpoints | `GET /public/sales/unsubscribe/{token}` (masked email, sender, already-unsubscribed) and `POST` (one-click, RFC 8058: the form body is deliberately never read), both rate-limited 30/min per IP, idempotent. Suppresses the address for **that workspace only** and stops every active enrollment for it there. Footer link points at the FE page `/unsubscribe/{token}`; `List-Unsubscribe` points at the API POST. A GET that accepts `text/html` (a mail client or browser opening that URL) gets a `303` to the FE page, for a validly signed token only; the FE's own JSON fetch gets the status. | Requiring a login or a confirm step on the POST (breaks one-click) |
| Headers | `From: "{sender} at {startup}" <SALES_OUTREACH_FROM_EMAIL>`, `Reply-To: {sender account email}`, our own `Message-ID: <{send id}@{domain}>`, `X-Cofoundaz-Step: {send id}`, `List-Unsubscribe`, `List-Unsubscribe-Post`. Names/subjects are collapsed to one line (no header injection). Footer (startup name, optional postal address, unsubscribe link) is appended server-side. | |
| Bounce intake | Scheduled IMAP poll reads INBOX after the highest ledgered UID **for the current UIDVALIDITY**. The first run for a UIDVALIDITY starts at the current UIDNEXT (a `baseline` ledger row): the inbox may be shared, so history is never read. Headers are peeked (`BODY.PEEK[HEADER]`) and the full body is fetched only for `multipart/report; report-type=delivery-status` candidates. A message counts only if it is `multipart/report; report-type=delivery-status` with a **5.x.x** status (4.x.x is ignored). It is tied to our send by `X-Cofoundaz-Step`, else our `Message-ID`, in the returned original; the reported recipient must match the one we mailed. The send is marked `bounced`, the address suppressed, the workspace's enrollments for it stopped. Unattributable reports are ledgered `unmatched` and change nothing (no workspace is known). | Suppressing by the DSN's recipient alone (cross-workspace damage) |
| **Bounce idempotency** | `sales_imap_processed (uidvalidity, uid)` is **claimed first** (unique, `ON CONFLICT DO NOTHING`), the effect applied, both committed together; only then is the mail **copied** to `SALES_OUTREACH_IMAP_PROCESSED_FOLDER` and flagged `\Seen \Flagged` in place. **Nothing is ever deleted or expunged** (the mailbox may be a shared inbox). A re-poll, a crash, or a refused move cannot count a bounce twice; a failed fetch stops the batch so later UIDs are not ledgered past an unread one. Everything else (normal mail, DMARC aggregate reports) is ledgered `ignored` and left untouched. | Relying on the IMAP `\Seen` flag (a human opening webmail would hide it) |
| Auto-pause | After each bounce: if >10% of the workspace's last 50 sequence sends bounced (minimum sample 10, so 1 of 1 is not 100%), `sales_settings.paused_at` is set and founders get `sales.sending.paused` ("Sales emails paused: too many bounces", CTA Review → `/sales/sequences`). Founders resume with `POST /sales/sending/resume`. **Complaint-based pausing is not built**: a plain mailbox has no complaint feed. | |
| Fairness | Each tick interleaves due steps across workspaces and splits the global hourly cap between the workspaces waiting (`cap // waiting`, whole cap when only one waits), so a big backlog cannot take every slot. Limit: if a workspace sends alone it may use the full cap, and another workspace whose steps become due later waits for the rolling hour. | Strict global FIFO |
| Scheduler | `scheduled.sales.outreach_send` and `scheduled.sales.outreach_bounces`, one job per 5-minute bucket (the existing `scheduled_runs` claim), enqueued **only when the env is configured**. | A new always-on loop in the worker |
| Not configured | Unset/incomplete env → `GET /sales/sending` says `configured:false, reason:"not_configured"`; sequences save, enroll and show; the tick does nothing; a follow-up send is `409 SENDING_NOT_CONFIGURED`. Never a 500. `console` provider is never "configured" in production; `none` SMTP security is refused in production. | |
| Sender identity (lead call 1) | The enrolling member, frozen as name + email on the enrollment; if they left, the sequence creator, then the first founder (refreshed on the enrollment). | |
| Stop rules | replied / stopped (manual), unsubscribed, bounced, suppressed, sequence archived, last step sent, and the deal-stage rule: `stop_on_stage_change` sequences stop when a deal whose `contact_id` is the enrolled contact moves stage (`move_stage` hook). Converting a lead re-points its running enrollments at the new contact. | |
| AI drafts | `sales_drafts` + job `ai.sales.draft` (budget-metered, strict JSON `{subject, body}`, dashes stripped, unknown merge tags removed). Step drafts append nothing. A follow-up draft is sent **once** (claimed under a draft row lock, released if the mail never left) through the same sender and guardrails and logged as an `email` activity on the deal. | |

## What's involved

| Area | Paths |
|---|---|
| Config | `app/core/config.py` — `SALES_OUTREACH_*` (names below) |
| Models + migration | `app/db/models/sales_sequences.py` (`EmailSequence`, `SequenceEnrollment`, `SequenceSend`, `SalesSuppression`, `ImapProcessed`, `SalesDraft`), `alembic/versions/0068_sales_sequences.py` |
| Mail platform | `app/platform/outreach.py` (sender, MIME, IMAP mailbox) |
| Services | `app/services/sales/{sequences,sending,suppression,bounces,unsubscribe,tokens,outreach_render,drafts,draft_prompts}.py`; hooks in `deals.move_stage` and `leads.convert_lead` |
| Worker | `app/worker/handlers/sales_outreach.py`, `app/worker/scheduler.py` (`_due_sales_outreach`), `app/worker/__main__.py` |
| API | `app/api/v1/endpoints/sales_sequences.py` mounted at `/sales` and `/public/sales` (`app/api/v1/api.py`) |
| Notifications / activity | `sales.sending.paused` (founders), activity entity types `sales_sequence`, `sales_enrollment` (SALES_ROLES only) |
| Tests | `tests/services/sales/test_{sequences,sending,bounces,unsubscribe,tokens}.py`, `tests/platform/test_outreach.py`, `tests/api/sales/test_sequences_api.py`, `tests/worker/test_sales_outreach_scheduler.py`, `tests/test_sales_sequences_migration.py`; fakes in `tests/outreach_fakes.py` (in-process SMTP socket server, stub IMAP mailbox, DSN builder). `tests/conftest.py` blanks every `SALES_OUTREACH_*` setting for every test so a developer's `.env` can never reach the real mailbox. |
| E2E | `e2e/test_sales_b.py`, captures `e2e/_captures/sales_b/`; `scripts/e2e_run.sh` blanks the mailbox env for the server and the test process |

## Operate

Env (names only; values live in the env files and are never read by tooling):

| Var | Meaning |
|---|---|
| `SALES_OUTREACH_PROVIDER` | `smtp` (real) or `console` (dev, logs only). Unset = not configured. |
| `SALES_OUTREACH_SMTP_HOST` / `_PORT` / `_SECURITY` | `ssl` on 465 or `starttls` on 587 (`none` is for local/e2e and refused in production). Hosts are normalised at config load (scheme and trailing slash stripped; a non-hostname fails boot); security is validated; certificates are verified with default hostname checking. Mail host per lead: `mail.cofoundaz.com`; from the dev network 465 is filtered, 587 and 993 are open. |
| `SALES_OUTREACH_SMTP_USERNAME` / `_PASSWORD` | the mailbox login (also used for IMAP) |
| `SALES_OUTREACH_FROM_EMAIL` | the From address; its domain is what `GET /sales/sending` reports |
| `SALES_OUTREACH_IMAP_HOST` / `_PORT` | bounce reading; port 993 = TLS, any other port = STARTTLS |
| `SALES_OUTREACH_HOURLY_LIMIT` | global hourly cap for the mailbox (default 30) |
| `SALES_OUTREACH_IMAP_PROCESSED_FOLDER` | optional, default `Cofoundaz-Processed` |
| `SALES_OUTREACH_UNSUBSCRIBE_KEY` | **required in production when `SALES_OUTREACH_PROVIDER` is set (boot fails otherwise)**: signs unsubscribe links; dev falls back to `SECRET_KEY`. Generate once, never rotate (rotating breaks every link already in inboxes). |

Also needed in production for correct links: `API_PUBLIC_URL` (List-Unsubscribe target) and `APP_BASE_URL` (footer link).
Deliverability is DNS work on the mailbox domain (SPF, DKIM, DMARC) and is outside this code.

Roll back: set `SALES_OUTREACH_PROVIDER` empty (sending stops, everything else keeps working). Schema:
`alembic downgrade 0066_finance_invoice_automation` drops the six tables (data loss for sequences/ledger).

## Verification

- Unit/integration on scratch DBs: services (render, caps, leases, retries, suppression at send time, rejection →
  bounce, bounce-rate pause, follow-up guardrails), a **real two-session concurrency test** (a second worker skips
  the leased step while the first is inside SMTP), SMTP sender against an in-process socket server (delivery,
  550 → permanent, 4xx/554 → transient, unreachable → transient), DSN parsing + poll idempotency (re-poll, UIDVALIDITY
  change, unmatched, wrong recipient, cross-workspace), token tamper cases, API + permission sweep (every route 401
  without auth, 403 for mentor/consultant/accountant), unsubscribe end to end from a message that was actually sent.
- Live e2e `e2e/test_sales_b.py` against real uvicorn + Postgres: see the FE guide's verification table.
- **NOT verified live:** delivery through the real cPanel mailbox (TLS, AUTH, SPF/DKIM/DMARC alignment) and reading
  real bounce mail over IMAP (the e2e DSN is synthetic and goes through a stub mailbox). Both need the real env on
  staging and one test send + one bounce to a dead address.

## Follow-ups

- Real-mailbox smoke on staging once the env is set: one send to a test inbox, one to a nonexistent address, confirm
  the DSN is parsed (cPanel's DSN wording is unseen) and the bounce moves to the processed folder.
- **Send windows (business hours / weekdays):** the spec defines none, so none are enforced; add if wanted (needs a per-workspace timezone and a decision on weekends).
- Complaint feedback / ARF reports (D-SAL-8's "3 complaints in 7 days") has no source on a plain mailbox; add if a feedback-loop
  address becomes available.
- Reply detection stays a manual **Mark replied** (no inbound parsing, by design).
- Per-workspace daily cap (100) and global hourly cap are constants/env, not UI settings.
