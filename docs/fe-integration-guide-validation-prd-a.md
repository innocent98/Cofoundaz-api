# FE integration guide: Validation Hub PRD slice (a)

Overview stat strip + riskiest-assumption nudge, **"Design experiment"** (AI draft), **hosted smoke-test pages at
`/p/{slug}`** (D-VAL-1) with real visit / CTA / signup capture, the server-side funnel and verdict, the waitlist,
the signup-milestone notification, and deletes. Gap list rows 2-6 and 14 (`cofoundaz/docs/checklist/validation-prd-gaps.md`).

Every body below is pasted from a live run: `e2e/test_validation_prd_a.py` against the running app (stub LLM), captures
in `e2e/_captures/validation_prd_a/`. One extra capture uses the **real model** (marked). Ids are throwaway test data.
Member routes need `Authorization` + `X-Workspace-Id` and the **founder or team_member** role (others `403`).
Public routes (`/validation/pages/...`) need **no headers at all**.

## 1. `GET /validation/overview` (PRD 09.1 stat strip + 09.6 nudge)
`200`, `overview.json`:
```json
{"data": {"assumptions": {"untested": 2, "testing": 0, "validated": 0, "invalidated": 0},
  "live_experiments": 0, "responses_collected": 0,
  "riskiest_untested": {"id": "44e0592d-8931-4bd6-9c69-f14aa9a8bc11",
    "statement": "Market associations will promote us to members", "risk": "high"}}, "meta": null}
```
- `riskiest_untested` is the untested assumption with the highest risk (oldest first on a tie), or **`null`** when
  nothing is untested: hide the nudge then. Nudge copy (PRD): **"Your riskiest untested assumption: '{statement}'.
  Want an experiment for it?"** CTA **"Design experiment"**.
- `responses_collected` = survey responses **plus** smoke-test waitlist signups (after one signup,
  `overview_after_signup.json` shows `"responses_collected": 1, "live_experiments": 1`).

## 2. "Design experiment": `POST /validation/assumptions/{id}/experiment-draft`
`202`, `experiment_draft_queued.json`: `{"data": {"job_id": "8d22d3b0-…", "status": "queued"}, "meta": null}`.
Poll `GET /jobs/{job_id}` (every 2 s, stop at `succeeded`/`failed`, ~60 s cap). `data.result`:

| `result.status` | What to do |
|---|---|
| `ready` | open the smoke-test builder **pre-filled** from `result.draft` (nothing is saved yet) |
| `gone` | the assumption was deleted meanwhile: close the flow |
| `over_budget` | show the over-budget copy ("AI is at its daily limit, try again tomorrow.") |

Real-model capture (`experiment_draft_job_real_model.json`, **REAL MODEL**, local stack):
```json
{"data": {"id": "599490f0-…", "type": "validation.experiment.draft", "status": "succeeded",
  "result": {"draft": {"name": "Same-Day Settlement Paid Pilot", "cta_text": "Reserve paid access",
    "headline": "Settle Your Market Trades the Same Day",
    "rationale": "A paid-access click shows traders are willing to commit money, directly testing whether same-day settlement is worth a fee.",
    "metric_type": "preorder_click",
    "subheadline": "NovaPay will let market traders settle eligible trades today for a small per-trade fee.",
    "assumption_ids": ["e51c6768-bb1f-44a1-863b-08590e0c72bf"],
    "slug_suggestion": "novapay-same-day-settlement-paid-pilot", "target_conversion": 5},
   "status": "ready", "assumption_id": "e51c6768-bb1f-44a1-863b-08590e0c72bf"}, "error": null}, "meta": null}
```
Map `draft` → builder: `name` → test name, `headline`/`subheadline`/`cta_text` → step 1, `metric_type`/`target_conversion`
→ step 2, `slug_suggestion` → step 3 slug, `assumption_ids` → link. Show `rationale` as a one-line "why this test".
`target_conversion` is an integer 1-50. The stub capture `experiment_draft_job.json` has the same shape.

## 3. Build, then publish a smoke test (existing endpoints, new fields)
`POST /validation/experiments` with `type: "smoke_test"` and `config` (the builder already sends these keys):
`headline, subheadline, cta_text, require_email, show_pricing, post_signup_message, metric_type (email_signup |
cta_click | preorder_click), target_conversion, slug`. The response (and every experiment) now has two **top-level**
fields: `"slug"` and `"published_at"`. Publish with `PATCH /validation/experiments/{id}` `{"status": "live"}`.
`smoke_published.json` (`200`):
```json
{"data": {"id": "ea12c7db-0942-4801-8907-1b019d19ab2c", "name": "Same-day settlement", "type": "smoke_test",
  "status": "live", "config": {"slug": "novapay-same-day", "cta_text": "Join the waitlist",
  "headline": "Get paid the same day", "metric_type": "email_signup", "subheadline": "Instant settlement for market traders",
  "show_pricing": false, "require_email": true, "target_conversion": 10}, "metrics": {},
  "assumption_ids": ["44e0592d-8931-4bd6-9c69-f14aa9a8bc11"], "slug": "novapay-same-day",
  "published_at": "2026-10-19T19:59:32.402718+00:00", "created_at": "…", "updated_at": "…"}, "meta": null}
```
**Field-nesting trap:** the slug is sent inside `config.slug` but read back from the **top-level `slug`** (that's the
one that is live). The public URL is `{APP_ORIGIN}/p/{slug}`; show it with copy + open buttons once `status` is `live`.

Slug errors (show under the slug field):
- `422` `slug_invalid_422.json`: `{"error": {"code": "VALIDATION_ERROR", "message": "Use 3 to 60 lowercase letters,
  numbers and dashes, not starting or ending with a dash.", "field_errors": [{"field": "config.slug", "message": "…same…"}]}}`.
  Reserved words (admin, api, app, www, p, s, help, support, login, signup, cofoundaz) get "That address is reserved. Try another."
- `409` `slug_taken_409.json`: `{"error": {"code": "SLUG_TAKEN", "message": "That page address is taken. Try another.", "field_errors": []}}`.
  A slug your workspace used before (even on a deleted test) stays yours; another workspace's never becomes available.

A test may be `live` **without** a slug (numbers typed in by hand for a page hosted elsewhere); it just has no `/p/` page.

## 4. The public page `/p/{slug}` (FE route, no login, like `/s/[token]`)
### `GET /validation/pages/{slug}` (60/min per IP)
`200`, `public_page.json`:
```json
{"data": {"headline": "Get paid the same day", "subheadline": "Instant settlement for market traders",
  "cta_text": "Join the waitlist", "require_email": true, "show_pricing": false, "metric_type": "email_signup",
  "post_signup_message": "You're on the list, we'll be in touch soon.", "powered_by": "Powered by Cofoundaz"}, "meta": null}
```
Draft, ended, deleted and unknown slugs are all the same `404` (`public_unknown_slug_404.json`:
`{"error": {"code": "NOT_FOUND", "message": "Not found.", "field_errors": []}}`): show a plain "This page isn't
available" with no workspace detail. Always render the `powered_by` footer.

### Visitor id
Generate **one random id per browser** (e.g. `crypto.randomUUID()`, stored in `localStorage` under a per-slug key) and
send it on every event. Visits and clicks are counted per distinct id; events without one are rejected.

### `POST /validation/pages/{slug}/events` (60/min) `{"kind": "visit"|"cta_click"|"preorder_click", "visitor_id": "<8-64 chars>"}`
`201` `public_event_visit.json`: `{"data": {"received": true}, "meta": null}`. Send `visit` once on page load; `cta_click`
when the CTA is pressed; `preorder_click` on a fake-door pricing button (`show_pricing`). Bad body `422`
(`public_event_bad_kind_422.json`): `field_errors` `[{"field": "kind", "message": "Input should be 'visit', 'cta_click' or 'preorder_click'"}, {"field": "visitor_id", "message": "Field required"}]`.
Fire-and-forget: never block the page on these.

### `POST /validation/pages/{slug}/signups` (20/min) `{"email": "...", "visitor_id": "..."}`
`201` `public_signup.json`: `{"data": {"message": "You're on the list, we'll be in touch soon."}, "meta": null}`.
Show `data.message` (it is the founder's custom message when set). A **repeat email gets the identical reply**
(`public_signup_repeat.json`) and is stored once; never tell the visitor it was a duplicate. Bad email `422`
(`public_signup_bad_email_422.json`): the server text is "value is not a valid email address: An email address must have an @-sign."
**Don't show that raw text to visitors**: validate client-side and show "Enter a valid email address."

## 5. Founder reads
### `GET /validation/smoke-tests/{id}/stats`
`200`, `smoke_stats.json` (after 4 visit events from 3 browsers, 1 CTA click, 1 signup):
```json
{"data": {"experiment_id": "ea12c7db-…", "name": "Same-day settlement", "status": "live", "slug": "novapay-same-day",
  "visits": 3, "signups": 1, "cta_clicks": 1, "preorder_clicks": 0, "metric_type": "email_signup",
  "conversion": 33.3, "target_conversion": 10, "sample_needed": 100, "verdict": null}, "meta": null}
```
- `conversion` uses the test's `metric_type` (signups, CTA clicks or preorder clicks over visits). Hand-entered
  `metrics.visits/signups` still add on top, for tests hosted elsewhere.
- **The verdict is now the server's** (D-VAL-4): `verdict` stays `null` until `visits >= sample_needed` (100), then
  `"validated"` or `"weak"`. Show the PRD banner only when non-null: **"Signal: {conversion}% conversion against your
  {target_conversion}% bar → looks {verdict}."** Before that: "{visits} of {sample_needed} visitors so far". Delete the
  FE's own verdict calculation.

### `GET /validation/smoke-tests/{id}/signups` (waitlist)
`200`, `smoke_waitlist.json`: `{"data": {"signups": [{"email": "funke@example.com", "signed_up_at": "2026-10-19T19:59:32.447688+00:00"}]}, "meta": null}`.
Oldest first; emails are lower-cased. Offer a CSV download client-side.

## 6. Signup milestone notification
When a waitlist reaches 10, 25, 50, 100, 250, 500 or 1000 signups (D-VAL-5), every founder and team member gets
notification type `validation.smoke_test.milestone`, title **"{test name} just passed {n} signups"** (body empty).
Deep-link it to `/validation/smoke-tests?test={experiment_id}`; the payload carries `experiment_id`. Not captured
live (needs 10 real signups); copy is unit-tested (`test_milestone_notification_copy`), the event in
`test_signup_milestone_event`.

## 7. Deletes (row 14)
`DELETE /validation/assumptions|experiments|interviews|surveys/{id}` → `200` `{"data": {"deleted": true}, "meta": null}`
(`experiment_deleted.json`). Unknown or other-workspace id `404` (`assumption_delete_again_404.json`). Effects:
deleting an assumption removes it from every interview's and experiment's `assumption_ids`; deleting an experiment
takes its page offline and **deletes its waitlist emails**; deleting a survey deletes its responses. Ask before
deleting ("This also deletes its waitlist of {n} emails." for a smoke test with signups).

## 8. Verification
| Behaviour | Live? | Source |
|---|---|---|
| Overview counts + riskiest + null case | ✅ / ❌ unit (null) | `overview.json`, `overview_after_signup.json`; `test_overview_with_nothing_to_test` |
| Design experiment queued + draft result (stub) | ✅ | `experiment_draft_queued.json`, `experiment_draft_job.json` |
| Design experiment with the real model | ✅ | `experiment_draft_job_real_model.json` (local stack) |
| `gone` / `over_budget` draft results | ❌ unit | `tests/api/test_validation_prd_a.py` |
| Publish sets `slug` + `published_at` | ✅ | `smoke_published.json` |
| Public page, events, signups, repeat, 422s, 404 | ✅ | `public_*.json` |
| Stats from events + verdict null below sample | ✅ | `smoke_stats.json` |
| Verdict validated / weak at the sample size, cta_click metric | ❌ unit | `test_verdict_only_once_the_sample_is_big_enough`, `test_cta_click_metric_drives_conversion` |
| Waitlist | ✅ | `smoke_waitlist.json` |
| Slug invalid 422 / taken 409 | ✅ | `slug_invalid_422.json`, `slug_taken_409.json` |
| Released slug stays with its workspace | ❌ unit | `test_a_released_slug_stays_with_its_workspace` |
| Milestone notification | ❌ unit | see §6 |
| Deletes + 404 | ✅ (experiment, assumption) / ❌ unit (interview, survey, unlinking) | `experiment_deleted.json`, `assumption_delete_again_404.json`; `test_deletes_clean_up_links_and_events` |
| Public routes rate-limited | ❌ unit | `test_public_page_routes_are_rate_limited` |
