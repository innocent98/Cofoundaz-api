# FE integration guide: Settings (Profile, Security, Workspace, Notification schedule)

Every body below is pasted from a real response captured against the running API by `e2e/test_settings.py`
(captures in `e2e/_captures/settings/`, re-derivable with `scripts/e2e_run.sh`). The email, ids and timestamps
in the captures are throwaway values from a fresh e2e user. Billing is a separate guide
(`fe-integration-guide-settings-billing.md`). Base path `/api/v1`. Envelope: success `{data, meta}`, error
`{error:{code,message,field_errors[]}}`.

## What this is, in the FE's terms

The Settings hub's non-Billing tabs. **Profile** (edit name, role, country, phone, photo), **Security** (change
password, two-factor on/off, backup codes, "where you're signed in"), **Workspace** (name, website, industry,
stage, logo; founder only) and the **notification schedule** (daily/weekly digest and quiet hours; stored, not
sent yet). Every route is open to every role except where noted, so an accountant or mentor who reaches
`/settings` gets Profile and Security.

Auth: every route needs `Authorization: Bearer <access token>`. Workspace and notification routes also need
`X-Workspace-Id`. Profile and Security routes do **not** (they belong to the person, not the workspace).

## Profile

### `PATCH /auth/me` (200)
Send only the fields that change. `country` is a two-letter code (any case, stored upper). `country` and
`phone` accept `null` to clear; `full_name` and `role_title` can't be blank.
```json
{"full_name": "Ada Obi", "role_title": "CEO", "country": "ng", "phone": "+2348012345678"}
```
Response (the same shape as `GET /auth/me`; **`phone` is new**):
```json
{
  "data": {
    "user": {
      "id": "71a03878-3c50-4072-ac24-ca31efb7cceb",
      "email": "delivered+e2e-ab063a386f32@resend.dev",
      "status": "active"
    },
    "profile": {
      "full_name": "Ada Obi",
      "role_title": "CEO",
      "country": "NG",
      "avatar_url": null,
      "phone": "+2348012345678"
    },
    "memberships": [
      {
        "startup_id": "319eca7c-5c7c-49ff-801c-ed3562e64afc",
        "name": "Cofoundaz",
        "role": "founder"
      }
    ],
    "active_workspace_id": "319eca7c-5c7c-49ff-801c-ed3562e64afc"
  },
  "meta": null
}
```

**Trap:** unknown fields and an empty body are now **422** (before, extras were silently ignored, so a typo looked
like a save):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "nickname",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "country",
        "message": "Use a two-letter country code, like NG."
      }
    ]
  }
}
```

### `POST /auth/me/avatar` (200, multipart field `file`) and `DELETE /auth/me/avatar` (200)
PNG, JPG or WebP, up to 2 MB, checked by content not by the declared type. Both return the `GET /auth/me` shape.
```json
{
  "data": {
    "user": {
      "id": "71a03878-3c50-4072-ac24-ca31efb7cceb",
      "email": "delivered+e2e-ab063a386f32@resend.dev",
      "status": "active"
    },
    "profile": {
      "full_name": "Ada Obi",
      "role_title": "CEO",
      "country": "NG",
      "avatar_url": "var/storage/avatars/71a03878-3c50-4072-ac24-ca31efb7cceb/aab3960dcb614be799034bf79782b9a6.png",
      "phone": "+2348012345678"
    },
    "memberships": [
      {
        "startup_id": "319eca7c-5c7c-49ff-801c-ed3562e64afc",
        "name": "Cofoundaz",
        "role": "founder"
      }
    ],
    "active_workspace_id": "319eca7c-5c7c-49ff-801c-ed3562e64afc"
  },
  "meta": null
}
```
Not an image (bytes don't match), 422:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That file isn't a valid image.",
    "field_errors": []
  }
}
```
```json
{
  "data": {
    "user": {
      "id": "71a03878-3c50-4072-ac24-ca31efb7cceb",
      "email": "delivered+e2e-ab063a386f32@resend.dev",
      "status": "active"
    },
    "profile": {
      "full_name": "Ada Obi",
      "role_title": "CEO",
      "country": "NG",
      "avatar_url": null,
      "phone": "+2348012345678"
    },
    "memberships": [
      {
        "startup_id": "319eca7c-5c7c-49ff-801c-ed3562e64afc",
        "name": "Cofoundaz",
        "role": "founder"
      }
    ],
    "active_workspace_id": "319eca7c-5c7c-49ff-801c-ed3562e64afc"
  },
  "meta": null
}
```

## Security

### Two-factor
`GET /auth/mfa` (200). `enabled_at` and the count are `null`/`0` when off:
```json
{
  "data": {
    "enabled": false,
    "method": null,
    "enabled_at": null,
    "backup_codes_remaining": 0
  },
  "meta": null
}
```
```json
{
  "data": {
    "enabled": true,
    "method": "totp",
    "enabled_at": "2026-10-27T12:44:46.322838+00:00",
    "backup_codes_remaining": 10
  },
  "meta": null
}
```

Turn on is the existing flow: `POST /auth/mfa/totp/setup` then `POST /auth/mfa/totp/verify` (returns the 10 backup
codes once). **New:**

- `POST /auth/mfa/backup-codes/regenerate` `{password}` (200) returns a fresh set of 10 and invalidates the old
  set. Show them once, exactly like setup. A wrong password is **422** `CURRENT_PASSWORD_WRONG`:
```json
{
  "error": {
    "code": "CURRENT_PASSWORD_WRONG",
    "message": "That isn't your current password.",
    "field_errors": []
  }
}
```
  Response shape (codes redacted in the capture): `{"data":{"backup_codes":["0000000001", ...]},"meta":null}`.
- `POST /auth/mfa/disable` `{password, code}` (200). `code` is a current authenticator code **or** an unused
  backup code. Wrong code 401, off already 409:
```json
{
  "data": {
    "enabled": false
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "MFA_INVALID_CODE",
    "message": "That code isn't right. Try again.",
    "field_errors": []
  }
}
```
```json
{
  "error": {
    "code": "MFA_NOT_ENABLED",
    "message": "Two-factor isn't on.",
    "field_errors": []
  }
}
```
- Both re-ask for the password and share a lock: **5 wrong passwords in 15 minutes returns 429
  `TOO_MANY_ATTEMPTS`** ("Too many attempts. Try again in 15 minutes."). Not verified live (needs 5 failures; unit
  tested).

**UX:** turning 2FA off does not sign anyone out. Use a confirm dialog that asks for password + code.

### Change password: `POST /auth/password/change` (200)
`{current_password, new_password, refresh_token?}`. The refresh token is only needed when the refresh cookie
isn't sent (the app normally relies on the cookie). Every **other** sign-in is revoked; this one stays alive.
`other_sessions_signed_out` counts how many were revoked.
```json
{
  "data": {
    "changed": true,
    "other_sessions_signed_out": 0
  },
  "meta": null
}
```
Errors, all 422 unless noted: `CURRENT_PASSWORD_WRONG`, `WEAK_PASSWORD` (same policy as signup), `PASSWORD_UNCHANGED`
(new equals current, unit tested), 429 `TOO_MANY_ATTEMPTS` after 5 wrong current passwords (unit tested).
```json
{
  "error": {
    "code": "CURRENT_PASSWORD_WRONG",
    "message": "That isn't your current password.",
    "field_errors": []
  }
}
```
```json
{
  "error": {
    "code": "WEAK_PASSWORD",
    "message": "Add a number and make it at least 8 characters.",
    "field_errors": []
  }
}
```

**Trap:** if neither the cookie nor `refresh_token` identifies this device, **every** session is revoked including
this one, and the next refresh fails with 401. The FE should then send the user to `/login` with a "Password
changed, sign in again" note.

### Where you're signed in
`GET /auth/sessions` (200): one row per live sign-in, newest first. `id` is stable across token refreshes. `current`
is true for the row matching the refresh cookie; if the cookie isn't sent no row is current.
(The capture's ids are placeholders; real ids are UUIDs. `label` is "Chrome on Mac" style, derived from the
browser's User-Agent, "Browser on unknown device" when it can't tell.)
```json
{
  "data": [
    {
      "id": "00000000-0000-0000-0000-000000000002",
      "label": "Browser on unknown device",
      "ip": "127.0.0.1",
      "signed_in_at": "2026-10-27T12:44:49.122525+00:00",
      "expires_at": "2026-11-26T12:44:49.150292+00:00",
      "current": false
    },
    {
      "id": "00000000-0000-0000-0000-000000000001",
      "label": "Browser on unknown device",
      "ip": "127.0.0.1",
      "signed_in_at": "2026-10-27T12:44:43.844718+00:00",
      "expires_at": "2026-11-26T12:44:44.382961+00:00",
      "current": true
    }
  ],
  "meta": null
}
```
- `DELETE /auth/sessions/{id}` (200) signs that device out. Revoking an unknown, already revoked or someone else's
  id is **404** (no way to probe other people's sessions):
```json
{
  "data": {
    "revoked": true
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
- `POST /auth/sessions/revoke-others` (200) keeps this device. **409 `CURRENT_SESSION_UNKNOWN`** when the cookie
  doesn't identify it (send `{"refresh_token": "..."}` in the body, or sign in again; unit tested).
```json
{
  "data": {
    "revoked": 1
  },
  "meta": null
}
```

## Workspace

`GET /workspace` (200, any member). `can_edit` is true for founders only:
```json
{
  "data": {
    "id": "319eca7c-5c7c-49ff-801c-ed3562e64afc",
    "name": "Cofoundaz",
    "description": null,
    "website": null,
    "logo_url": null,
    "industry": "Fintech",
    "business_model": "b2b",
    "stage": "idea",
    "country": null,
    "can_edit": true
  },
  "meta": null
}
```

`PATCH /workspace` (200, **founder only, others 403**). Fields: `name`, `description` (1000), `website`
(http/https), `industry`, `business_model`, `stage`, `country`. `website`, `description`, `industry` and
`country` accept `null` or `""` to clear. Unknown fields and an empty body are 422.
```json
{
  "data": {
    "id": "319eca7c-5c7c-49ff-801c-ed3562e64afc",
    "name": "Nova Pay",
    "description": null,
    "website": "https://novapay.example",
    "logo_url": null,
    "industry": "Fintech",
    "business_model": "b2b",
    "stage": "build",
    "country": "NG",
    "can_edit": true
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "website",
        "message": "Enter a web address starting with https://."
      }
    ]
  }
}
```
`POST /workspace/logo` (200, founder only, multipart `file`, PNG/JPG/WebP up to 2 MB; **SVG is not accepted
here**, unlike the onboarding logo route). Returns the workspace object:
```json
{
  "data": {
    "id": "319eca7c-5c7c-49ff-801c-ed3562e64afc",
    "name": "Nova Pay",
    "description": null,
    "website": "https://novapay.example",
    "logo_url": "var/storage/logos/319eca7c-5c7c-49ff-801c-ed3562e64afc/e3619029ed984604a307c257c22065a5.png",
    "industry": "Fintech",
    "business_model": "b2b",
    "stage": "build",
    "country": "NG",
    "can_edit": true
  },
  "meta": null
}
```

## Notification schedule (storage only)

`GET /notifications/preferences` now also carries the digest and quiet-hours fields (per user per workspace):
```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": true,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true
    },
    "digest_mode": "realtime",
    "digest_time": "08:00",
    "digest_day": 0,
    "quiet_enabled": false,
    "quiet_from": "20:00",
    "quiet_until": "06:00",
    "timezone": "Africa/Lagos"
  },
  "meta": null
}
```
`PUT /notifications/preferences` accepts any subset of: `master_email`, `categories`, `digest_mode`
(`realtime`|`daily`|`weekly`), `digest_time` ("HH:MM"), `digest_day` (0 = Monday .. 6), `quiet_enabled`,
`quiet_from`, `quiet_until`, `timezone` (an IANA name).
```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": true,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true
    },
    "digest_mode": "weekly",
    "digest_time": "07:30",
    "digest_day": 4,
    "quiet_enabled": true,
    "quiet_from": "21:00",
    "quiet_until": "06:30",
    "timezone": "Africa/Lagos"
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "digest_mode",
        "message": "Input should be 'realtime', 'daily' or 'weekly'"
      }
    ]
  }
}
```

**UX the FE must be honest about:** nothing sends a digest yet and nothing enforces quiet hours yet. These
settings are saved and returned. Do not promise "you'll get a digest at 07:30" until the digest sender ships
(tracked in the gap list as S7, owned by Notifications).

## Verification table

| Behaviour | Verified |
|---|---|
| Profile patch (all four fields), unknown-field 422, bad country 422 | live (captures above) |
| Avatar upload / remove / fake bytes 422 | live |
| Avatar over 2 MB, SVG rejected | unit tests only |
| 2FA status, regenerate, disable (code), wrong code 401, off 409, wrong password 422 | live |
| Disable with a backup code; 5-strike 429 lock | unit tests only |
| Change password (200, old password dead, this device's refresh still works) | live |
| `PASSWORD_UNCHANGED`, 429 lock, no-cookie revokes everything | unit tests only |
| Sessions list with `current`, revoke one, revoke again 404, revoke others | live |
| `CURRENT_SESSION_UNKNOWN` 409, revoking someone else's session 404 | unit tests only |
| Workspace get / patch / logo / bad website; non-founder 403 | live except 403 (unit) |
| Notification digest + quiet hours save and read back, bad digest 422 | live |
| Digest email actually sent, quiet hours enforced | **not built** |
