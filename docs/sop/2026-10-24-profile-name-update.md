# SOP: members can set their display name (`PATCH /auth/me`)

| | |
|---|---|
| **What shipped** | `PATCH /api/v1/auth/me` `{full_name}`: sets the caller's display name and returns the `GET /auth/me` payload. Branch `feature/profile-name-update`. |
| **Date** | 2026-10-24 (unblocks Academy re-walk D2, FE #152) |

## Why
The name was only ever written in founder onboarding step 1 (`app/services/onboarding/steps.py`). Invited members skip that, so they had no name, and a member with no name who finished a course got a permanent "Cofoundaz member" certificate. The FE had no route to fix it (the re-walk's team member had `full_name: null`).

## How
- `MePatch` in `app/api/v1/endpoints/auth/me.py`: `full_name` only, `extra="ignore"` (an `email` in the body is never applied), whitespace collapsed, 1 to 120 characters (the certificate fitting cap), control characters rejected. Errors use `PydanticCustomError` so the message has no "Value error, " prefix.
- `GET` and `PATCH` share `_me_payload`, so the FE can replace its cached `me` with the PATCH response.
- Rejected: reusing the onboarding state PATCH (it advances onboarding steps and needs a workspace).

## What's involved
`app/api/v1/endpoints/auth/me.py`; `tests/api/auth/test_me.py`; FE guide `docs/fe-integration-guide-profile.md`; captures `e2e/_captures/profile/`.

## Verification
Unit tests (set + normalise, 422 blank/long/control chars, unknown fields ignored, 401). Live captures from the branch running in the dev image against the dev DB (the re-walk team member really had no name and now reads "Rewalk Teammate").

## Operate / roll back
Nothing to run; revert to roll back.

## Follow-ups
Settings page: reuse this route for the profile form (role title, country, avatar would extend `MePatch`).
