# SOP: Legal & Compliance, Slice 1 (entity formation, IP tracker, compliance calendar)

| | |
|---|---|
| **What shipped** | The three data-backed surfaces of PRD Module 13 under `/api/v1/legal`: a per-jurisdiction formation checklist whose steps live in code, IP-asset CRUD with derived expiry, and compliance-item CRUD with a derived due status, drift-free recurrence and an optional private proof of filing — plus two scheduled reminders (compliance at 14/7/1 days and on going overdue; IP renewals at 14/7/1). Branch `feat/legal-compliance`, migration `0106_legal`. |
| **Date** | 2026-11-13 (checkpoints settled 2026-10-15; spec approved 2026-10-17) |
| **Design** | `docs/superpowers/specs/2026-10-17-module-13-legal-compliance-slice1-design.md` (D1..D24) · plan `docs/superpowers/plans/2026-10-22-module-13-legal-compliance-slice1.md` · gap list FE repo `docs/checklist/legal-prd-gaps.md` (D-LEG-1..8) |
| **Not in this slice** | 13.2 Contract Templates and 13.3 AI Contract Review (slice 3), 13.6 Legal Advisor Requests (slice 2). Their FE pages exist against the mock hook. |

## Why
Legal had three mock pages and no API. A founder could not record a filing deadline, an IP renewal or a formation step
anywhere in the product, and compliance deadlines in Nigeria carry real penalties. Slice 1 makes the deadlines real and
notifies before they pass.

## How (and decisions)
- **Two role sets, which is new.** Legal is the first module whose read set is wider than its write set: `LEGAL_ROLES`
  (founder + team member + **legal advisor**) on every GET, founder + team member on every write (D1). `_legal_reader` and
  `_legal_writer` are separate dependencies — do not collapse them into one boolean on the FE either.
- **The formation catalog is code** (`app/services/legal/catalog.py`), like `health_score/config.py`. Only a startup's
  progress is stored, and **a row's existence IS the tick** (D7). The unique key is
  `(startup_id, jurisdiction, step_key)`, so switching jurisdiction and switching back never wipes ticks (D8).
- **Money is integer minor units + an ISO-4217 code**, following Finance (D5). Both `null` together means "we do not know
  the fee" — never a `0` that reads as "free".
- **Statuses are derived on read, never stored** (D9, D12). An IP asset stores `drafting|filed|registered` and `expired`
  is computed from `renewal_on` against today; a compliance item stores `open|done` and `upcoming|due_soon|overdue|done`
  is computed from `due_on`. Order matters: a done filing is never overdue, and one due *today* is `due_soon`, not
  overdue. **"Today" is the UTC date** — the same clock the reminders use, so the badge and the notification can never
  disagree (D4).
- **Recurrence cannot drift (D16/D24).** `anchor_day` is stored on the row and the next occurrence is computed from the
  anchor, not from the previous date: a series on the 31st reads 31 Jan → 28 Feb → **31 Mar**. Computing from the previous
  occurrence would walk backwards and never recover. Marking a recurring item done spawns the successor; the UNIQUE on
  `previous_item_id` is what makes a second spawn a no-op rather than a duplicate filing.
- **Proof of filing is private (PR review, 2026-11-13).** The upload **ignores the client's `Content-Type`** and classifies
  by magic bytes (`sniff_proof`), stores via `save_private`, and the row carries the API **route**, not a storage address —
  `proof_url` on the row stays NULL and is computed on read, mirroring Finance's `receipt_url`. The first implementation
  copied `attach_receipt` before Finance gained `sniff_receipt`/`save_private`; that is the bug this fixed.
- **Reminders reuse the existing scheduler.** `_due_compliance` and `_due_ip_renewals` feed `scheduler_tick`; idempotency is
  `_claim(task_key, scope_key, period_key)` — **no "already sent" flags of our own**. When several thresholds qualify at
  once only the most urgent fires, and the looser ones are burned via `also_claim` so they cannot fire a day later (D21).
  IP renewals are bounded **below** by today, or a long-past renewal would satisfy every window and fire a `due1`.
- **Notifications are scoped through `AUDIENCE`**, which drives both fan-out and the read filter — so a row written before
  a rule landed is hidden rather than deleted. The four excluded roles are 403 on every legal route, so delivering to them
  would leak exactly what the routes withhold.
- **`legal` email defaults OFF, reversing the spec's D20.** Develop's D-NOT-2 rule (post-dating the spec) says a category
  added after the original five starts with email off, so nobody begins receiving a new kind of email unasked. In-app is
  always on.
- **Rejected / deferred:** `legal_entities` and the entity summary card (PRD 13.1 wants them; no FE exists — D-LEG-3 area);
  IP renewal auto-creating a linked compliance item (PRD 13.4; would double-notify against the reminder already built);
  an overdue-count endpoint (every row carries its derived badge, so the FE counts its own banner — D13).
- **Deviations from the PRD, declared:** `drafting` added to the PRD's Filed/Registered/Expired (D-LEG-8);
  `legal.ip.renewal_due_soon` is not in the PRD's events list (D18); the catalog disclaimer is **adapted** from §1.4 rather
  than quoted, because §1.4 says "This is AI-assisted guidance…" and nothing in the catalog is AI-generated (D-LEG-7).

## What's involved
`app/db/models/legal.py`, `alembic/versions/0106_legal.py`, `app/services/legal/{catalog,formation,ip,compliance,recurrence,errors}.py`,
`app/schemas/legal.py`, `app/api/v1/endpoints/legal.py` (+ `app/api/v1/api.py`), `LEGAL_ROLES` and four enums in
`app/db/models/enums.py`, `app/worker/scheduler.py` (`_due_compliance`, `_due_ip_renewals`, `Due.also_claim`),
`app/worker/handlers/scheduled.py` (two handlers), and the notification `audience` / `registry` / `categories` trio.

## Verification
- **211 tests**: models (13), services (101 across catalog/formation/ip/compliance/recurrence), API (52 + 16 proof + 9
  notifications), worker (18), migration (2).
- **Live e2e** `e2e/test_legal.py` — one journey covering all three surfaces, the legal-advisor read/write split, a mentor
  403 and a cross-tenant 404; **20 captures** in `e2e/_captures/legal/`, which are the verbatim source for
  `docs/fe-integration-guide-legal.md`.
- **Local gates**: ruff, black, isort, mypy (whole `app`, 533 files), bandit; migration upgrade/downgrade/upgrade and a
  from-zero migrate on a scratch `cofoundaz_e2e` database; one alembic head.
- **Running the e2e on Windows:** `scripts/e2e_run.sh` needs `docker compose` **and** `poetry`, which on a Windows host
  live on opposite sides of the container wall. Instead, boot uvicorn **inside** `cfz-test` (it has the dependencies and
  mounts the repo read-write) against an isolated `cofoundaz_e2e` DB, with `EMAIL_FILE_DIR` on the container's **own**
  filesystem (`/tmp/mail-e2e`, not the bind mount), then run pytest in the same container. Set `E2E_MAIL_DIR` **inside**
  the container — passing it as `docker exec -e` lets Git Bash rewrite `/tmp/...` into a Windows path and the mailbox
  silently reads an empty directory.

## Finish round (2026-11-13, lead-finished on the same branch)
| Change | Detail |
|---|---|
| Proof download (D-LEG-5) | `GET /legal/compliance-items/{id}/proof` (`app/api/v1/endpoints/legal.py`, `compliance.read_proof`). Reader roles (founder, team member, legal advisor). Streams the private file with the sniffed content type, `Content-Disposition: attachment`, `nosniff`, `no-store`, a sandbox CSP. 404 for no proof, another workspace, or a missing file; 403 for the excluded roles. `proof_url` is this same route. No signed links: the FE fetches with the bearer token. |
| Reminders re-arm | `ScheduledRun.scope_key` is now `{id}:{due_on}` (compliance) and `{id}:{renewal_on}` (IP), so a moved date is a new obligation. Before, the key was the row id alone, so an IP reminder fired once per asset forever (a yearly renewal never re-fired). Old claims stay as harmless history. Rejected: deleting claims when a date moves (needs a hook in every write path). |
| D-LEG-6 | `legal.compliance.overdue` in `CRITICAL_TYPES` (PRD 20.2, locked on; safe because its audience is role-restricted); all three legal events in the `deadlines` filter. |
| CI fixes | Em dash removed from `ESTIMATE_DISCLAIMER`; legal events added to `READ_ROLES` in `test_dashboard_audience.py`; CodeQL `py/side-effect-in-assert` sites in `e2e/test_legal.py` now assign before asserting. |
| Verification | New tests in `tests/api/test_legal_proof.py` (roles, cross-tenant, headers), `tests/worker/test_legal_scheduler.py` (move date re-arms, unchanged date does not duplicate), `tests/api/test_legal_notifications.py`. Live journey re-run on a scratch Postgres; two new captures. Fast local checks only; CI ran the full suite. |

## Disclaimer copy (2026-11-13, `fix/legal-disclaimer-copy`)
| Change | Detail |
|---|---|
| What | `ESTIMATE_DISCLAIMER` (`app/services/legal/catalog.py`) now ends "...involve a licensed professional." The pointer "(you can find one in the Marketplace)" is gone. |
| Why | Marketplace is a hidden module and stays hidden until Adebayo says otherwise; the disclaimer must not send founders to a page they cannot open. |
| Captures | `formation_checklist.json` and `formation_step_ticked.json` re-captured from a live run of `e2e/test_legal.py` (they still carried the older em-dash text). Other captures from that run were reverted: only id/timestamp churn. |
| Guard | `tests/services/legal/test_catalog.py::test_disclaimer_points_at_no_hidden_module`. |
| FE | The FE renders `disclaimer` from the API, so nothing to change there for Formation/Compliance. The hidden `/legal/review` page had its own copy of the sentence; removed in the FE re-walk PR. |
| Unhiding Marketplace | If Marketplace is unhidden later, a pointer can come back as a decision, not by default. |

## Operate / roll back
No new env vars. Migration `0106_legal` is chained on `0105_analytics_custom_metrics`; develop's numbering is no longer
sequential (`0105` chains on `0101`, `0102` on `0103`), so **re-chain against `alembic heads`, not against the highest
number**, and re-check immediately before pushing — develop moved five times during this build. Roll back:
`alembic downgrade 0105_analytics_custom_metrics` (drops the three tables) or revert the branch. A downgrade does **not**
remove proof files already in storage; those keys are orphaned and must be cleared separately.

## Follow-ups
- Scheduler, from the PR review: a batch cap plus a `ScheduledRun` anti-join on `_due_compliance`; deleting the stored
  proof **after** the DB commit rather than before.
- D-LEG-1..8 are lead defaults dated 2026-11-13 (Adebayo may overrule); the gap list in the FE repo records them.
- PRD gaps carried into later slices: `legal_entities` + entity summary card, IP renewal → linked compliance item, `owner`
  and linked docs on a compliance item, and proof as a Documents record (`proof_doc_id`) rather than a key on the row.
- The FE calls none of this yet: `hooks/useLegalApi.ts` is still a mock. See the gap list for the 47 rows.
