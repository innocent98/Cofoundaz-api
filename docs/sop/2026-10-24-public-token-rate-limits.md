# SOP: per-caller rate limits on public token routes

| | |
|---|---|
| **What shipped** | `GET /verify/{code}`, `GET` + `POST /validation/surveys/{token}[/responses]`, `GET` + `POST /public/sales/unsubscribe/{token}` now limit per caller across all tokens. Branch `fix/public-token-rate-limits`. |
| **Date** | 2026-10-24 (Academy re-walk defect D3, FE #150) |

## Why
slowapi's default `key_style="url"` scopes each limit to the full request path, path parameter included. `/verify/A` and `/verify/B` had separate 30/min buckets, so a caller could try unlimited codes (the re-walk saw 30 x 404 then 429 on one code while another code still answered). The tokens are high-entropy (60-bit codes, 256-bit survey tokens, HMAC unsubscribe tokens), so guessing was impractical, but the documented "per caller" limit was not true.

## How
- `@limiter.shared_limit(rate, scope="public-...")` on those five routes: one bucket per caller (the limiter's key: user id for a bearer token, else client address) per route, whatever the token.
- **Rejected:** `Limiter(key_style="endpoint")` globally. It would also change the default limit on every authenticated `/{id}` route to one bucket per function and could 429 normal browsing.
- **Left per-URL on purpose:** smoke-test pages `/validation/pages/{slug}` (+ events, signups). Slugs are public, and a per-caller bucket across all pages could throttle real campaign traffic from shared addresses.
- Client addresses are real in staging (forwarded headers are honoured; e.g. the CI runner's address appears in API logs), so per-caller limits do not collapse into one global bucket.

## What's involved
| Area | Files |
|---|---|
| Routes | `app/api/v1/endpoints/verify.py`, `validation.py`, `sales_sequences.py` |
| Tests | `tests/api/test_rate_limit.py::test_public_token_routes_limit_per_caller_not_per_token` (failed for all five before the fix) |

## Verification
New parametrised test sends `allowed + 1` requests with a different token each time and expects the last to be 429. Full local CI gates (see the PR).

## Operate / roll back
Nothing to run. Revert the commit to roll back.

## Follow-ups
None.
