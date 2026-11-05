# Marketplace slice B: listings, availability and the founder directory

**What shipped:** experts can list services (consultation or package) and set their availability; staff review listings (including edits to live ones) in the Admin queue; founders and team members browse experts, profiles, listings and open time slots. Reports can now target a listing or an expert. API only, no booking or money. FE guide `docs/fe-integration-guide-marketplace-listings.md`. Branch `feature/marketplace-listings`, PR: see the branch. Migration **`0094_marketplace_listings`**, chained on `0093_readiness_mock_qa` (develop head when last merged); the funding PR chains 0095 onto whichever merges first (one `down_revision` in `alembic/versions/0094_marketplace_listings.py`; the migration tests downgrade relative to their own revision, so they survive that).

**Why:** slice A made experts trustworthy; founders still had nothing to look at. Slice B gives the directory and the supply side its content, so the FE can build the founder pages against real data while bookings (C) and money (C, E) are built. Design: `cofoundaz/docs/superpowers/specs/2026-11-05-marketplace-design.md` §3.1-§3.3, §4, §5, §6, §9.

**How**

| Topic | Decision |
|---|---|
| Visibility (one place) | `directory._visible`: expert `approved` AND listing `active`. Suspending an expert hides them on the next request; an expert with no live listing has no card and no profile (404). Founder payloads are explicit allow-lists built in `directory.py`; a test asserts the exact key sets and greps responses for every staff-only value (email, user id, vetting notes, pre-screen, commission, review message, meeting link, pending edit). |
| Roles (D-MP-L1) | `MARKETPLACE_ROLES` = founder + team member (`require_role`), everyone else 403. Mutations that need the founder alone arrive with bookings. |
| Listing lifecycle | `draft → pending_review → active ⇄ paused`, `rejected` (editable, resubmittable), `unlisted` (staff only). Staff send-back of a first submission: reject → `rejected`, request changes → `draft`. |
| Live edits (D-MP-L8) | Price, duration, delivery days, revisions, meeting link and tags apply at once. Title, summary, description, category and deliverables go to `pending_revision_json` and the queue while the approved version stays live; approving applies it, rejecting or asking for changes discards it. Setting a field back to its live value cancels it. `pending_revision_json` is `JSONB(none_as_null=True)`: a first version stored JSON `null` and the queue's `IS NOT NULL` filter matched it (caught by a test). |
| Category rule | Legal and Accounting listings require that to be the expert's vetted main category (design §1.3.6); other categories must be a main or secondary one. |
| Pre-screen (D-MP-L9) | `prescreen_listing`: the profile rules over title, summary, description, deliverables and tags plus a price chip (outside N1,000 to N10,000,000). Chips for staff, never a block. Money is a strict integer in kobo up to N100,000,000; strings and floats are refused. |
| Earnings preview | `commission_for`: 15% (or the expert's override) rounded half up to the kobo; net + commission always equals the price (tested over edge prices). |
| Slot generator | `slots.py`, pure maths. Rules and exceptions are wall-clock in the expert's timezone, converted to UTC per date, so DST windows are their real length (spring-forward 00:00-06:00 = 5 slots, fall-back = 7), a time inside the spring-forward gap resolves with `fold=0`, half-hour offsets and local/UTC date differences work. `slot_min` is the start step, independent of session length; overlapping windows dedupe; blocked windows overlap-test (edge touching is allowed); min notice and buffer apply. 21 worked-example tests. `busy_intervals` is the hook slice C fills with bookings. |
| Availability storage | Weekly rules and exceptions in two tables; `min_notice_h` and `buffer_min` are columns on the provider (one schedule per expert). `PUT` replaces everything in one transaction. |
| Directory | SQL filters (category, price band, rating with 3+ reviews, language, query), sort and page in Python (relevance is deterministic: ratings once there are 3, then profile completeness), `available=true` = a real slot in the next 7 days. Fine for tens of experts; move sort/paging into SQL past a few hundred. |
| Ratings | `rating = {avg, count, is_new}`; `avg` is null until 3 reviews (D-MP-7). No reviews exist yet. |
| Files for founders | Avatar and portfolio files are served through `/marketplace/providers/{id}/avatar` and `…/portfolio/{item}/file` with auth, only for visible experts, only clean files, and only the avatar / portfolio purposes (a credential can't be reached through a portfolio item). |
| Reports | `ReportItemType` gains `listing` and `provider` (`review` stays out until slice F, so it is a plain 422 and the existing admin-reports journey and tests are untouched). Founders can report what the directory shows them (OWN_ITEM refused, hidden items 404, `review` 404 until slice F). Staff `remove` on a listing report **unlists** it (audited on the listing, `via: report`, with the actor), `restore` relists it **only if the report flow did the unlisting** (marker reason; a moderator's own unlist is never undone by a report restore); on a provider report it is refused (`USE_VETTING`: suspend through vetting). Staff cannot remove, restore, dismiss or escalate a report about their own listing or profile. |
| Flagged live edits | Price and tags apply at once, but a pre-screen flag that wasn't accepted at the last approval is stored in `prescreen_json["review"]` and the listing joins the queue (`has_flag`, `flagged_edits` count). Approve accepts the live state as the new baseline (`prescreen_json["accepted"]`); request changes / reject close the item without taking the listing down; unlist takes it down. |
| Type rules | Consultation fields (`duration_min`, `meeting_url`) and package fields (`delivery_days`, `deliverables`) can't cross types (422 `FIELD_NOT_FOR_TYPE`); a live listing can't lose its type's required fields (422 `FIELD_REQUIRED`). |
| Staff actions | Approve / reject / request changes: reason only. Unlist / relist: reason + the staff member's own TOTP (they change what founders can buy). The 404 check runs before the code is verified. Every action audited (`marketplace.listing.*`), expert emails for approve / reject / changes. Staff cannot act on their own listings (`CANNOT_ACT_ON_SELF`, same rule as slice A). |

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `MarketplaceListing`, `MarketplaceAvailabilityRule`, `MarketplaceAvailabilityException` in `app/db/models/marketplace.py`; enums `ListingType`, `ListingStatus`, `AvailabilityKind`, `MARKETPLACE_ROLES`, new `ReportItemType` values in `app/db/models/enums.py`; `alembic/versions/0094_marketplace_listings.py` (adds the 3 tables, incl. `unlisted_prior_status` and `unlisted_via_report_id` on listings, and `min_notice_h`, `buffer_min` on providers) |
| Services | `app/services/marketplace/{slots,availability,listings,listing_moderation,directory,takedown}.py`; additions to `prescreen.py`, `emails.py`, `constants.py` |
| API | `app/api/v1/endpoints/{provider_listings,admin_marketplace_listings,marketplace}.py`, schemas in `app/schemas/marketplace.py`, wired in `app/api/v1/api.py` |
| Reports | `app/services/reports/{founder,core}.py`, `app/services/staff/reports.py` |

**Verification:** `tests/services/marketplace/test_slots.py` (21 worked examples), `tests/api/test_marketplace_listings.py` (63: guards, validation matrix, lifecycle, live edits, moderation, self-act guard, availability), `tests/api/test_marketplace_directory.py` (37: roles, visibility for every status, no-leak key sets, filters, sort, paging, slots, files, reports), `tests/test_marketplace_listings_migration.py` (up, down one, up, `alembic check`), existing report and slice A suites still pass. Live journey `e2e/test_marketplace_listings.py` on a real server (60 captured bodies). Local gates clean; CI runs the full suite and scans.

**Operate / roll back:** no new config or secrets. To revert, downgrade `0094` (drops the three tables and the two provider columns, so all listings and schedules are lost) and revert the PR. The Marketplace stays hidden in the FE sidebar.

**Follow-ups**

- Slice C fills `availability.busy_intervals` with bookings and adds slot holds; the booking snapshot copies title, price and deliverables.
- Provider-side listing images and a public (logged-out) directory are not built.
- Reviews (slice F) set `rating_avg` / `rating_count`; the card already renders "New".
- Staff FE for the listing queue and the provider portal's Listings / Availability pages go to the FE developer by exact-scope brief after merge.
- Avatar and portfolio uploads by approved experts still go live without review (decided in slice A: name locked, flagged text edits queued).
