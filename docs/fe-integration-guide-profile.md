# FE integration guide: profile name (`PATCH /auth/me`)

Every body below is copied from a live capture against the running app (dev stack, 2026-10-24); the raw files are in
`e2e/_captures/profile/`. IDs and emails are test-account values; treat them as placeholders.

## 1. What this is
The member's display name. It is printed on Academy certificates, comments, mentions and invitations. Founders set it in onboarding
step 1, but **invited members skip founder onboarding and can have no name** (`profile.full_name: null` in `GET /auth/me`). Use this
endpoint to ask for it, for example before a member completes a course (Academy re-walk D2: otherwise the certificate is issued to
"Cofoundaz member" for good), and later from Settings.

## 2. `PATCH /api/v1/auth/me`
Auth: bearer token (any active user; no `X-Workspace-Id` needed). Body: `{"full_name": string}`. Unknown fields are ignored (an
`email` in the body is **not** applied). The name is trimmed and inner whitespace collapsed; 1 to 120 characters; no control characters.

**200** returns exactly the `GET /auth/me` shape, so you can replace your cached `me` with it (`me_patch_ok.json`):

Request body: `{
  "full_name": "  Rewalk   Teammate  "
}`

```json
{
  "data": {
    "user": {
      "id": "6087acf5-f674-447f-88a2-cd46baffe39a",
      "email": "rewalk-tm@example.com",
      "status": "active"
    },
    "profile": {
      "full_name": "Rewalk Teammate",
      "role_title": "Founder & CEO",
      "country": null,
      "avatar_url": null
    },
    "memberships": [
      {
        "startup_id": "11ba2aa0-8c8d-4035-adb8-62530988ed44",
        "name": "NovaPay",
        "role": "team_member"
      }
    ],
    "active_workspace_id": "11ba2aa0-8c8d-4035-adb8-62530988ed44"
  },
  "meta": null
}
```

## 3. Errors
**422** blank or whitespace-only (`me_patch_blank_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "full_name",
        "message": "Tell us your name."
      }
    ]
  }
}
```
**422** longer than 120 characters (`me_patch_too_long_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "full_name",
        "message": "Keep it to 120 characters."
      }
    ]
  }
}
```
A newline or other control character gives 422 with `"Use letters, spaces and punctuation only."` (unit-tested, not captured).

**401** no token (`me_patch_unauthenticated_401.json`):
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

## 4. Traps and UX
- `field_errors[].field` is `full_name` (no `body.` prefix): put the message under the name input.
- Show the **normalised** name from the response, not what was typed (`"  Rewalk   Teammate  "` comes back as `"Rewalk Teammate"`).
- `profile` can be `null` in `GET /auth/me` only for legacy rows; treat `profile == null` and `profile.full_name == null` the same: ask for a name.
- Certificates print the name at issue time and never change afterwards: tell the member that before they finish a course.

## 5. Verification
| Behaviour | Verified |
|---|---|
| 200 sets and normalises the name, returns the `me` shape | live |
| 422 blank, 422 > 120 chars | live |
| 401 without a token | live |
| 422 control characters, unknown fields ignored | unit tests (`tests/api/auth/test_me.py`) |
