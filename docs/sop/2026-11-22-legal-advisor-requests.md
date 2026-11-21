# SOP: Legal & Compliance, Slice 2 (Legal Advisor Requests)

| | |
|---|---|
| **What shipped** | PRD 13.6 / gap rows 41 to 47: a founder or team member asks a named legal advisor to review one document, the advisor reads it through an authenticated route, comments on it, and returns a marked-up draft; the founder can cancel while it is open. Seven routes under `/api/v1/legal/advisor-requests`, one new table, one notification. Branch `feature/legal-advisor-requests`, migration `0110_legal_advisor_requests`. |
| **Date** | 2026-11-22 (D-LEG-9..15 accepted 2026-11-18; D-LEG-16..18 accepted 2026-11-19 with two conditions) |
| **Design** | FE repo `docs/checklist/legal-prd-gaps.md` (D-LEG-9..18, rows 41 to 47) · slice 1 SOP `docs/sop/2026-11-13-legal-compliance-slice1.md` |
| **Not in this slice** | 13.2 Contract Templates and 13.3 AI Contract Review (slice 3, rows 27 to 40). Row 39's **API half** is done, see below. |

## Why
Legal had three working surfaces and a mock Requests page. A founder could not get a contract in
front of their lawyer inside the product, and the lawyer had no way to see one document without
being given the run of the workspace.

## How (and decisions)
- **The request IS the permission.** There is no `DocumentGrant` and no share token.
  `GET /advisor-requests/{id}/document` checks the request and streams the attachment, so
  cancelling ends access with nothing to revoke (D-LEG-16). This mirrors the shape accepted for
  slice 1's proof of filing (D-LEG-5), and it covers **both** attachment kinds, which a grant
  cannot: `DocumentGrant` and `DocumentVersion` exist only for rich documents, while an uploaded
  `DocumentFile` has neither.
- **`D-LEG-12` is superseded in part, and this is the single most important thing to know.** Its
  accepted answer said the reviewed draft becomes a new `DocumentVersion`. **It cannot.**
  `ShareAccess` is `view | comment` with no edit level, and every document write route is gated on
  `DOCUMENT_ROLES` (founder + team member), so an advisor can author no version by any path. A
  returned draft is therefore always a **new `DocumentFile` linked to the request**, for rich
  documents and uploads alike. Raised with the lead on 2026-11-19 and accepted.
- **Reads are scoped by role in one place.** `list_requests` and `get_request` take the viewer's
  role: writers see the workspace's requests, an advisor sees only the ones naming them, and an
  advisor asking for someone else's gets the same **404** as a foreign id. A separate
  `/assigned-to-me` route was rejected deliberately: it would have left the main route able to
  expose everything to an advisor if anyone forgot to scope it.
- **Two serializers, side by side.** `serialize_requests` (writers) and `serialize_for_advisor`
  (the advisor) live in the same file so the difference is visible. The narrow one drops `advisor`
  (always the viewer), renames `created_by` to a name-only `requested_by`, and carries **no user
  ids** — copying `documents._external_comment`. Resource ids stay, because the advisor needs them
  to fetch the document. People come from `owner_map`, whose own contract is "never any part of
  their e-mail", which satisfies D-LEG-15 by construction rather than by review.
- **The reviewed draft goes through Documents' upload** (`upload_file`), per the lead's condition
  2: its 15 MB cap, its type allowlist, a magic-byte sniff and the virus scan. That is why a
  lawyer can return a **`.docx`** — slice 1's proof sniff accepted PNG, JPEG and PDF only and
  would have refused the most likely file in the whole feature.
- **`in_review` moves on the advisor's FIRST COMMENT** (D-LEG-17), not on their first read: a
  stray click must not tell the founder their lawyer is working on it, and the status cannot be
  moved back. A founder's own comment never changes it, and a `returned` request is not dragged
  backwards.
- **One thread per request** on the shared `comments` table with `entity_type="advisor_request"`
  (D-LEG-13). No new table; the spare `anchor` column leaves room for per-version threads later
  without a migration.
- **An unclean file is 409 on create and 404 on read**, and the asymmetry is deliberate: on create
  the founder picked the wrong file and an actionable `FILE_NOT_CLEAN` helps them fix it, while on
  read the honest answer is simply that it is not available. D-LEG-18's accepted text says 404 for
  the read; `funding.data_room._target_of` already uses 409 for the create, and this follows both.
- **Cancel is terminal.** 409 on a second cancel and 409 on a completed review, following
  `documents.signatures.cancel_request`, which refuses anything not still awaiting. There is no
  reopen (D-LEG-14): raise a new request.
- **Row 39 needed no new code.** "Send to my legal advisor" from 13.3 is
  `POST /advisor-requests` with the contract's `document_id` or `file_id`; the FE finds the
  advisors from `GET /team/members`, which carries each role and is founder + team member only. A
  second endpoint would have been the same insert under a different name. Documented as §8 of the
  FE guide instead.
- **Rejected:** a separate advisor route (above); building file versioning in Documents (another
  team's module, and a much larger change); a notification when a draft comes back (rows 41 to 47
  specify only the status copy, and `document.file.uploaded` is not a notification spec, so there
  is no double-notify to avoid either).

## What's involved
`app/db/models/legal.py` (`AdvisorRequest`), `AdvisorRequestStatus` in `app/db/models/enums.py`,
`alembic/versions/0110_legal_advisor_requests.py`, `app/services/legal/advisor_requests.py`,
`app/schemas/legal.py` (`AdvisorRequestCreate`, `AdvisorRequestCommentCreate`),
`app/api/v1/endpoints/legal.py` (seven routes), and the notification trio
(`audience.py`, `categories.py`, `registry.py` plus `DIRECT` in
`tests/services/notifications/test_dashboard_audience.py`).

## Verification
- **74 API tests** (`tests/api/test_legal_advisor_requests.py`) and **7 model tests**
  (`tests/db/test_legal_models.py`): the role matrix on every route, tenancy, both serializer
  shapes, the CHECK constraint, the `in_review` transition and its non-transitions, the scan gate
  at both ends, and the cancel end-to-end.
- **9 notification tests** (`tests/api/test_legal_notifications.py`): only the named advisor is
  told, a removed advisor is told nothing, a non-advisor id in the payload is not a shortcut, and
  no `@` appears in the copy.
- **Live e2e** `e2e/test_legal_advisor_requests.py`, passed on the first run; **15 captures** in
  `e2e/_captures/legal_advisor_requests/`, which are the verbatim source for
  `docs/fe-integration-guide-legal-advisor-requests.md`.
- **Local gates**: ruff, black, isort, mypy (558 files). `alembic check` reports no new upgrade
  operations, so the hand-written migration matches the models. CI runs the full suite.

### Running the e2e on Windows
The slice 1 recipe still applies (`docs/sop/2026-11-13-legal-compliance-slice1.md`): boot uvicorn
**inside** `cfz-test` against an isolated `cofoundaz_e2e` database, keep `EMAIL_FILE_DIR` on the
container's own filesystem (`/tmp/mail-e2e`), and set `E2E_MAIL_DIR` **inside** the container,
because `docker exec -e` lets Git Bash rewrite `/tmp/...` into a Windows path.

**New failure mode, 2026-11-21.** A server started while the database was mid-migration wedged:
it held port 8010 without serving, so a fresh `uvicorn` failed with `[Errno 98] address already
in use` while every request to it timed out. The container has **no `ps` and no `pkill`**, so the
only cure is `docker restart cfz-test` — and the restart itself takes minutes, because Docker
waits out the wedged process's kill timeout. Migrate the database first, then start the server.

## Operate / roll back
No new env vars. Migration `0110_legal_advisor_requests` is chained on `0109_analytics_exports`;
develop's numbering is not sequential, so **re-chain against `alembic heads`, never the highest
number**, and re-check immediately before pushing. Roll back:
`alembic downgrade 0109_analytics_exports` (drops `advisor_requests`) or revert the branch. A
downgrade does **not** remove attachments or reviewed drafts: those are ordinary Documents rows
and stay in the library.

## Follow-ups
- **`MAX_DRAFT_BYTES` (15 MB) is duplicated** in `app/services/legal/advisor_requests.py` because
  `_MAX_FILE_BYTES` is private to Documents' endpoints module, exactly as `_CHUNK_BYTES` is
  duplicated across four endpoint modules today. A size cap that can drift is a real smell; worth
  promoting to a public constant in `app/services/documents/files.py` rather than copying it a
  third time.
- **`author.name` can be `null` while `requested_by.name` cannot.** The request serializer uses
  `owner_map`, which substitutes "A team member"; the comment serializer uses `team_svc._names`,
  which returns the raw value. The same person therefore reads differently in two places. Shared
  with Documents and Team, so it was not changed here, but the FE guide calls it out and it is
  worth aligning once.
- **No collapsing on repeated requests.** A founder raising three requests with the same advisor
  produces three notifications. Correct for this slice, and the lead cared about exactly this on
  IP renewals in slice 1, so it is worth a decision before volume grows.
- Rows deferred from 41 to 47: nothing. Row 44 and the FE halves of 43 and 46 are front-end work.
- Slice 3 (13.2 Contract Templates, 13.3 AI Contract Review, rows 27 to 40) must not start until
  this slice merges.
