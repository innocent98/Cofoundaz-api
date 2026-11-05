# Funding Hub: data room (slice B)

**What shipped:** the API behind the Funding Hub **Data room** page (PRD 14.2) and the public investor viewer: folders and items that reference Documents, link shares gated by an emailed 6-digit code (no investor accounts), view-only delivery with per-viewer PDF watermarks, a heartbeat, an access log and a `funding.dataroom.viewed` notification. API only; FE guide `docs/fe-integration-guide-funding-data-room.md`. Branch `feature/funding-data-room`. Migration **`0095_funding_data_room`** (parent `0092_marketplace_providers` for now; 0093 and 0094 are in flight, so the lead re-chains if they land first).

**Why:** the mock had a checkbox "watermark" with no effect, no investor entry path, no viewer route and no rate limits (gap rows 16 to 18). Design and calls: `cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md` sections 4 to 6; D-FUND-3 (email + code, no accounts), D-FUND-8 (downloads off, PDFs stamped), D-FUND-L4, L5, L6.

**How**

| Topic | Decision |
|---|---|
| References, not copies (L4) | `data_room_items` point at a `document_files` or `documents` row (CHECK: exactly one). Whether an item may be shown is decided by `data_room.live_target` on every read and every serve: same workspace, not trashed, files `clean`. |
| Link token | `HMAC-SHA256(SECRET_KEY, "dataroom:{share_id}:{version}")`, 256 bits, same pattern as Documents shares. Only its SHA-256 is stored (unique index); the founder can re-copy the link because it is recomputed. Rotating `SECRET_KEY` kills every outstanding link. |
| Uniform 404 | Unknown, tampered, revoked and expired links return the identical body on every `/dr` route. |
| No email enumeration | `POST /otp` loads the share with a row lock and every request, invited address or not, inserts exactly one challenge row (a stranger's is a consumed decoy) and counts against the hourly cap of 5, so the work and the cap do not depend on the email. Over the cap the answer is still the same 202 and nothing is sent (never an email-dependent 429). Mail is sent after the response. A partial unique index allows one live (unconsumed) code per share. `/verify` returns one 401 for wrong code, wrong email, expired, reused or no code, comparing bytes (a non-ASCII email is a 401, not a 500). |
| Code | 6 digits from `secrets`, keyed-HMAC hashed at rest (`data_room_challenges`), 10 minutes, single use, 5 tries, a new request replaces the old code. |
| Brute force | Per share: 5 code requests/hour, 10 failures against a live code lock the share for 15 minutes (`failed_attempts`, `locked_until`; guesses with no live code are not counted, so a link holder cannot lock the room; `/verify` takes a row lock so guesses serialise). Per IP (slowapi, `request.client.host`): `/otp` 10/hour, `/verify` 8/minute and 20/hour, content 120/hour, public reads 60/minute. |
| Session | HMAC-signed claim (`share id`, email, expiry), header `X-Data-Room-Session`, 2 hours at most and never past the share expiry. It proves who the viewer is; it never authorises by itself. |
| Serve-time authorisation | `data_room_serve.authorise` runs on `open`, `content` and `heartbeat`, re-reading the share (`db.refresh`) and the Documents row. Revocation, expiry, removal, a narrowed scope, trashing or a failed scan cut a viewer off on the next call. |
| Signed URLs | `open` logs the view and returns a 120-second ticket URL bound to share, item, view and email. The ticket is not trusted alone: `content` still re-authorises. Storage URLs are never handed out (no redirect to Cloudinary), so nothing outlives revocation. |
| Watermark (D-FUND-8) | PDFs and rich Documents (rendered with `render_pdf`) are stamped on every page, diagonally and in the footer, with the viewer's verified email and the UTC time (`pypdf` + `reportlab`). The overlay is placed on the page's crop box (correct for offset boxes). Encrypted, damaged or over-300-page PDFs are refused (422 `DOCUMENT_UNAVAILABLE`), never served bare. **Downloads off (default):** only PDFs, rich Documents and PNG/JPEG (converted to a PDF page with Pillow, then stamped) can be shown; every other type is refused before a view is logged with 422 `VIEW_ONLY_UNSUPPORTED` (lead default under D-FUND-8, Adebayo can overrule). Items carry `viewable`, and a share reports `unviewable_items` so the founder is warned in advance. **Downloads on:** other types are served as they are, unwatermarked; `?download=true` gives `attachment` only then. `watermark` is not a switch. |
| Access log | One `data_room_views` row per opened document (viewer email, a name snapshot, start, `duration_s`, keyed IP hash). The heartbeat adds the elapsed time, capped at 45 seconds a beat. No IP is returned by the API. |
| Notification (L6) | `funding.dataroom.viewed` is published on the first open of a share each UTC day, decided by an atomic `UPDATE ... WHERE last_viewed_at IS NULL OR < today RETURNING` so concurrent opens publish once; audience F + TM (no actor exclusion, the viewer is not a user). |
| Rejected | An `otp_required` column (always true under D-FUND-3); a `round_id`-scoped share filter; redirecting to storage signed URLs (breaks instant revoke and watermarking); extending `document_shares` (breaks its XOR check and public page). |

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/data_room.py` (5 tables), `app/db/models/__init__.py`, `alembic/versions/0095_funding_data_room.py` |
| Schemas | `app/schemas/data_room.py` |
| Services | `app/services/funding/{data_room,data_room_shares,data_room_gate,data_room_serve,watermark}.py` |
| API | `app/api/v1/endpoints/funding_data_room.py` (founder), `funding_data_room_public.py` (public), both mounted at `/funding` in `app/api/v1/api.py` |
| Notifications | `app/services/notifications/{registry,audience}.py`, `tests/services/notifications/test_dashboard_audience.py` |

**Verification:** `tests/api/test_funding_data_room_api.py` (133 tests, incl. refused-role and cross-workspace tests for every write route, the view-only policy, offset-box watermark, live-limiter tests for `/otp` and `/verify` across shares: role 403s, structure, validation, token hashed at rest, uniform 404, identical OTP answers, code hashing/expiry/reuse/tries/lock/hourly cap, IP throttle, session binding and expiry, ticket binding and expiry, watermark text, view-only types, download flag, rich Document, unstampable PDF, every serve-time cut-off, heartbeat cap, access log, notification once a day); `tests/test_funding_data_room_migration.py`; live journey `e2e/test_funding_data_room.py` (41 captures in `e2e/_captures/funding_data_room/`, the source of the FE guide). Local: black, isort, ruff, mypy, bandit; migration upgrade, downgrade, upgrade and `alembic check` on a fresh scratch DB. CI runs the full suite.

**Operate / roll back:** no new env var and no worker job. `APP_BASE_URL` must be the FE origin (the emailed link is `{APP_BASE_URL}/dr/{token}`) and the FE needs a public `/dr/[token]` route. Email goes through the platform sender (Resend in staging and prod). Revert by downgrading `0095` (drops the five tables; Documents files are untouched) and reverting the PR. To kill every link at once, rotate `SECRET_KEY` (also logs everyone out) or revoke shares individually.

**Follow-ups**

- The live journey's per-IP throttle (8 verifies/minute) trips before the 15-minute per-share lock, so the lock is unit-tested only. The limiter state is in process memory in the e2e server: restart it between journey runs.
- Folder-name uniqueness: the database enforces exact names; the case-insensitive rule is enforced by the service under a per-workspace lock (alembic cannot compare a unique expression index).
- Accepted as designed (Opus review): the content ticket travels in the query string and is replayable for 120 s (every fetch is re-authorised and stamped); the link token is derivable from the server key + share id so the founder can re-copy the link; slowapi has no Redis `storage_uri`, so per-IP limits are per worker (pre-existing, separate chore). Someone who already knows the invited email and holds the link can still burn the live code's 5 tries.
- Behind a proxy `request.client.host` may be the proxy address, which makes the per-IP limits shared. Confirm the deployment passes the real client address (same as the other public routes).
- Pitch-deck pinned item and the `readiness` share scope wait for Investor Readiness (spec section 9). A "viewed" chip on the pipeline card can read `GET /data-room/shares?investor_id=`.
- Office files (docx, xlsx, pptx) and text are not shown view-only (no conversion pipeline); the founder uploads a PDF or allows downloads (D-FUND-8 c would add conversion).
- Real Resend delivery and browser rendering of the stamped PDF were not exercised.
