# FE integration guide: Marketplace experts (providers) and Admin vetting (slice A)

Every body below is pasted from a live run against a real server: `e2e/test_marketplace_providers.py`, captures in `e2e/_captures/marketplace_providers/<name>.json` (the block title is the capture name). Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`, **except the hidden 404 on staff routes, which is `{"detail": "Not Found"}`**. Where something could not be exercised live, the section says so and names where its shape comes from.

Slice A is the **expert side and vetting only**: no listings, bookings or money exist yet, and the founder-facing Marketplace stays hidden in the sidebar. Design: `cofoundaz/docs/superpowers/specs/2026-11-05-marketplace-design.md`.

## What this is, in FE terms

1. **The expert portal (`/provider/*`).** An expert is a normal account (sign-up and email verification work as for anyone) that also has a Marketplace expert record. Experts have **no workspace**: do not send `X-Workspace-Id`, and do not run the onboarding wizard for them.
2. **Joining.** Two ways, one vetting queue: (a) **staff invite**: an Admin invites an email, the person gets a link `…/provider/invite/{token}`, signs in (or signs up with that same email) and accepts; (b) **open applications**: a signed-in account applies itself, only while the platform flag `MARKETPLACE_OPEN_APPLICATIONS` is on (it is **off** by default; `applications_open` on `GET /provider/status` tells the FE).
3. **Onboarding steps the portal should walk:** accept terms (done by the accept/apply call) → profile → upload credentials (and, for Design / Development / Marketing, add portfolio items) → submit → wait for review → (if asked) change and resubmit → approved.
4. **Admin console, scope `moderation` (Super Admin too).** A queue with counts by status, a detail page per application (profile, files, portfolio, checklist, history), a rubric checklist, and decisions: approve, reject, request changes, suspend, unsuspend; plus invite, resend, revoke.

## Status machine (the FE renders from `status`)

| status | Meaning | Expert can | Staff can |
|---|---|---|---|
| `invited` | Invited, or accepted and still filling in | edit profile, upload, submit | resend or revoke the invite (only before it is accepted); reject once submitted |
| `applied` | Applied through the open door, still filling in | edit profile, upload, submit | (same queue) |
| `in_review` | Submitted | **read only** (`PROFILE_LOCKED` 409 on edits and uploads) | tick checklist, approve, reject, request changes |
| `changes_requested` | Staff sent a message back | edit, upload, delete credentials, resubmit | approve is not available until resubmitted; reject allowed |
| `approved` | Vetted | edit profile (not the category), upload more documents | tick checklist (re-vetting), suspend |
| `suspended` | Paused | read only (`PROVIDER_SUSPENDED` 403 on edits) | unsuspend |
| `rejected` | Terminal | nothing: every `/provider/*` route except `/status` answers 403 `PROVIDER_REJECTED` | nothing |

Any other transition is `409 INVALID_STATE` (for example approving twice).

## Product calls built as written (design D-MP-1..9 accepted 2026-11-05; build-session defaults marked "default")

| # | Call |
|---|---|
| P1 (D-MP-1) | Hybrid supply: invite-only by default, open applications behind `MARKETPLACE_OPEN_APPLICATIONS`. |
| P2 (D-MP-2) | The portal is the same API and Next app under `/provider`. A provider is an account plus a `marketplace_providers` row, not a column on `users`. |
| P3 (D-MP-6) | Rubric per category. **Legal and Accounting** need `id`, `licence` (with an expiry date in the future) and **2** `references`. **Design, Development, Marketing** need `id`, `portfolio` and **1** `references`. Approve is refused until every required check is ticked. |
| P4 (D-MP-6) | The "Vetted" tooltip is generated from the checks actually ticked and **only appears once the expert is approved** (`badge.vetted` + `badge.checks`). Before approval `badge.checks` is `[]` for the expert. Never hard-code tooltip copy. |
| P5 (default) | Invite links last 14 days and are single use. Resending replaces the link. 50 invites per staff member per day. |
| P6 (default) | A reject deletes every uploaded file (NDPA: delete on rejection) and emails the expert. The expert's account stays; they just cannot use the portal. |
| P7 (default) | Credential files: PDF, PNG or JPEG, 15 MB each (avatars: PNG/JPEG, 5 MB), up to 10 credential and 24 portfolio files. Files are private: only the owner and (audited) moderation staff can read them. |
| P8 (default) | A secondary category list (max 3) may not contain Legal or Accounting unless it is also the expert's main category. After approval the main category is locked. |
| P9 (default) | Approve, reject, suspend and unsuspend need a reason (10+ chars) **and** the staff member's own fresh authenticator code (`totp_code`), like Reported-issues removals. Ticking the checklist, requesting changes, inviting, resending and revoking need no code. Every action writes an audit row. |
| P10 (default) | Terms: the call that joins (`accept` / `apply`) takes `accept_terms: true` and stores `terms_version` (`draft-2026-11` until counsel's text replaces it). |

## Auth rules the FE must handle on `/provider/*`

| Caller | Result |
|---|---|
| No token | 401 `UNAUTHORIZED` |
| Email not verified | 403 `EMAIL_NOT_VERIFIED` |
| Signed in but not an expert (founder, team member, staff, anyone) | 403 `NOT_A_PROVIDER` |
| Rejected expert | 403 `PROVIDER_REJECTED` |

`provider_me_anonymous_401`:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

`provider_me_not_a_provider_403`:
```json
{
  "error": {
    "code": "NOT_A_PROVIDER",
    "message": "This account isn't a Marketplace expert.",
    "field_errors": []
  }
}
```

`provider_me_rejected_403`:
```json
{
  "error": {
    "code": "PROVIDER_REJECTED",
    "message": "Your expert application was not approved.",
    "field_errors": []
  }
}
```

**Use `GET /provider/status` to decide whether to show the portal link**: it never 403s for a signed-in verified account.

## Expert endpoints

### `GET /provider/status`

Any signed-in, verified account. `status` is `null` when the account is not an expert.

`status_not_a_provider`:
```json
{
  "data": {
    "is_provider": false,
    "status": null,
    "applications_open": false
  },
  "meta": null
}
```

`status_invited`:
```json
{
  "data": {
    "is_provider": true,
    "status": "invited",
    "applications_open": false
  },
  "meta": null
}
```

`status_rejected`:
```json
{
  "data": {
    "is_provider": true,
    "status": "rejected",
    "applications_open": false
  },
  "meta": null
}
```

### `GET /provider/invites/{token}` (public, no auth)

What an invitation is for, so the FE can show "Ngozi, you're invited as a Legal expert" before sign-in. One `404 NOT_FOUND` for an unknown, expired, replaced **and** already-used token (it never says which). Rate limited per caller (30 per minute across all tokens).

`invite_preview_200`:
```json
{
  "data": {
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "display_name": "Ngozi Eze",
    "category": "Legal",
    "expires_at": "2026-11-19T14:46:58.364623+00:00",
    "terms_version": "draft-2026-11"
  },
  "meta": null
}
```

`invite_preview_replaced_link_404` (a link that staff replaced with "Resend"):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `POST /provider/invites/accept`

Signed in and verified, as **the invited email**. Body `{"token": "<from the link>", "accept_terms": true}`. The response is the expert's own record (same shape as `GET /provider/me`). Order of checks: bad/expired/used token → 400 `TOKEN_INVALID`; wrong account → 403 `INVITE_EMAIL_MISMATCH` **without spending the token**; account already an expert → 409 `ALREADY_PROVIDER`. Flow for a brand-new expert: sign up with the invited email, verify the email, log in, then accept. Save the token across the sign-up round trip (it is in the URL).

`invite_accept_wrong_account_403`:
```json
{
  "error": {
    "code": "INVITE_EMAIL_MISMATCH",
    "message": "This invitation was sent to a different email address.",
    "field_errors": []
  }
}
```

`invite_accept_terms_not_accepted_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "accept_terms",
        "message": "Value error, You need to accept the expert terms to continue."
      }
    ]
  }
}
```

`invite_accept_200`:
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "status": "invited",
    "source": "invite",
    "display_name": "Ngozi Eze",
    "headline": null,
    "bio": null,
    "category": "Legal",
    "categories": [],
    "languages": [],
    "timezone": "Africa/Lagos",
    "location": null,
    "avatar_file_id": null,
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "submitted_at": null,
    "review_message": null,
    "can_edit_profile": true,
    "can_submit": true,
    "submit_blockers": [
      {
        "field": "headline",
        "message": "Add a one-line headline."
      },
      {
        "field": "bio",
        "message": "Write a bio of at least 50 characters."
      },
      {
        "field": "languages",
        "message": "Add at least one language."
      },
      {
        "field": "credentials",
        "message": "Upload at least one credential document (ID or licence)."
      }
    ],
    "badge": {
      "vetted": false,
      "checks": []
    },
    "portfolio": [],
    "files": []
  },
  "meta": null
}
```

`invite_accept_replay_400` (the same link a second time):
```json
{
  "error": {
    "code": "TOKEN_INVALID",
    "message": "That link is invalid or has expired.",
    "field_errors": []
  }
}
```

### `POST /provider/apply` (201)

Body `{"category": "Design" | null, "accept_terms": true}`. Creates the same kind of record with `status: "applied"` and `source: "application"`.

**Not exercised live with the flag on.** The e2e server runs with open applications off, so only the closed answer is captured below. The success body has the same shape as `invite_accept_200` (asserted in the unit test `test_apply_when_open_feeds_the_same_queue`, not captured). Other errors (unit-tested, not captured): 409 `ALREADY_PROVIDER`, 409 `INVITE_PENDING` (an invitation for that email is waiting; send them to the link).

`apply_closed_403`:
```json
{
  "error": {
    "code": "APPLICATIONS_CLOSED",
    "message": "Expert applications aren't open right now.",
    "field_errors": []
  }
}
```

### `GET /provider/me`

The expert's own record. **Field-nesting notes:** it is flat (no `provider` wrapper); `badge` is the only nested object; `files` and `portfolio` are always arrays; `review_message` is a string only in `changes_requested`, `rejected` and `suspended`, otherwise `null` (present, never absent). Staff-only data (checklist authors, notes, pre-screen flags, history, commission) is **never** in this body.

- `can_edit_profile` / `can_submit`: drive which controls are enabled. `submit_blockers` lists what is missing (`{field, message}`), empty when ready; the same list returns as `field_errors` if the expert submits anyway.

`me_ready_to_submit` (all blockers cleared, credential uploaded):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "status": "invited",
    "source": "invite",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "category": "Legal",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "avatar_file_id": null,
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "submitted_at": null,
    "review_message": null,
    "can_edit_profile": true,
    "can_submit": true,
    "submit_blockers": [],
    "badge": {
      "vetted": false,
      "checks": []
    },
    "portfolio": [],
    "files": [
      {
        "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
        "purpose": "credential",
        "filename": "bar-licence.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-05T14:46:59.470952+00:00"
      }
    ]
  },
  "meta": null
}
```

`me_changes_requested` (note `review_message`; **`badge.checks` is `[]` for an unapproved expert**):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "status": "changes_requested",
    "source": "invite",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "category": "Legal",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "avatar_file_id": null,
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "submitted_at": "2026-11-05T14:46:59.646704+00:00",
    "review_message": "The licence scan is blurry; please upload a clearer copy.",
    "can_edit_profile": true,
    "can_submit": true,
    "submit_blockers": [],
    "badge": {
      "vetted": false,
      "checks": []
    },
    "portfolio": [],
    "files": [
      {
        "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
        "purpose": "credential",
        "filename": "bar-licence.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-05T14:46:59.470952+00:00"
      }
    ]
  },
  "meta": null
}
```

`me_approved`:
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "status": "approved",
    "source": "invite",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "category": "Legal",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "avatar_file_id": null,
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "submitted_at": "2026-11-05T14:47:00.253702+00:00",
    "review_message": null,
    "can_edit_profile": true,
    "can_submit": false,
    "submit_blockers": [],
    "badge": {
      "vetted": true,
      "checks": [
        "Identity verified",
        "Professional licence verified",
        "2 client references checked"
      ]
    },
    "portfolio": [],
    "files": [
      {
        "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
        "purpose": "credential",
        "filename": "bar-licence.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-05T14:46:59.470952+00:00"
      }
    ]
  },
  "meta": null
}
```

### `PATCH /provider/me`

Send only what changed. Unknown fields are a 422 (an expert cannot send `status`, `user_id`, `email`). Text fields are trimmed. Lists replace; send `[]` to clear. `timezone` must be an IANA name. Allowed: `display_name`, `headline` (160), `bio` (3000), `category`, `categories` (max 3), `languages` (max 8), `timezone`, `location`, `avatar_file_id` (id of the expert's own `avatar` upload).

`profile_patch_200`:
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "status": "invited",
    "source": "invite",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "category": "Legal",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "avatar_file_id": null,
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "submitted_at": null,
    "review_message": null,
    "can_edit_profile": true,
    "can_submit": true,
    "submit_blockers": [
      {
        "field": "credentials",
        "message": "Upload at least one credential document (ID or licence)."
      }
    ],
    "badge": {
      "vetted": false,
      "checks": []
    },
    "portfolio": [],
    "files": []
  },
  "meta": null
}
```

`profile_unknown_field_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "status",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```

`profile_bad_timezone_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Unknown timezone.",
    "field_errors": []
  }
}
```

`profile_regulated_secondary_422`:
```json
{
  "error": {
    "code": "REGULATED_CATEGORY",
    "message": "Legal and Accounting experts must be vetted for it as their main category.",
    "field_errors": []
  }
}
```

`profile_locked_in_review_409`:
```json
{
  "error": {
    "code": "PROFILE_LOCKED",
    "message": "Your application is under review. You can edit it if we ask for changes.",
    "field_errors": []
  }
}
```

`profile_category_locked_409` (approved expert changing category):
```json
{
  "error": {
    "code": "CATEGORY_LOCKED",
    "message": "You were vetted for this category. Contact support to change it.",
    "field_errors": []
  }
}
```

`profile_suspended_403`:
```json
{
  "error": {
    "code": "PROVIDER_SUSPENDED",
    "message": "Your profile is paused. Contact support.",
    "field_errors": []
  }
}
```

### `POST /provider/submit`

No body. Moves a draft (`invited`, `applied`, `changes_requested`) to `in_review` and emails a receipt. Returns the record. Needs: terms accepted, `display_name`, `headline`, a `bio` of 50+ characters, `category`, 1+ language, at least one **clean** credential file, and (non-regulated categories) at least one portfolio item.

`submit_incomplete_422`:
```json
{
  "error": {
    "code": "SUBMISSION_INCOMPLETE",
    "message": "A few things are still needed before you can submit.",
    "field_errors": [
      {
        "field": "headline",
        "message": "Add a one-line headline."
      },
      {
        "field": "bio",
        "message": "Write a bio of at least 50 characters."
      },
      {
        "field": "languages",
        "message": "Add at least one language."
      },
      {
        "field": "credentials",
        "message": "Upload at least one credential document (ID or licence)."
      }
    ]
  }
}
```

`submit_incomplete_design_422` (a Design expert also needs a portfolio item):
```json
{
  "error": {
    "code": "SUBMISSION_INCOMPLETE",
    "message": "A few things are still needed before you can submit.",
    "field_errors": [
      {
        "field": "credentials",
        "message": "Upload at least one credential document (ID or licence)."
      },
      {
        "field": "portfolio",
        "message": "Add at least one portfolio item."
      }
    ]
  }
}
```

`submit_200`:
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "status": "in_review",
    "source": "invite",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "category": "Legal",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "avatar_file_id": null,
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "submitted_at": "2026-11-05T14:46:59.646704+00:00",
    "review_message": null,
    "can_edit_profile": false,
    "can_submit": false,
    "submit_blockers": [],
    "badge": {
      "vetted": false,
      "checks": []
    },
    "portfolio": [],
    "files": [
      {
        "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
        "purpose": "credential",
        "filename": "bar-licence.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-05T14:46:59.470952+00:00"
      }
    ]
  },
  "meta": null
}
```

### Files

`POST /provider/files` (201) is `multipart/form-data` with `purpose` (`credential` | `portfolio` | `avatar`) and `file`. The server sniffs the **bytes**, not the `Content-Type` header. Refusals: 422 `UNSUPPORTED_FILE_TYPE`, 422 `FILE_CONTENT_MISMATCH` (also for an empty file), 422 `FILE_TOO_LARGE`, 409 `FILE_LIMIT_REACHED`, 422 `FILE_BLOCKED`, and 409 `PROFILE_LOCKED` while in review. A new avatar replaces the previous one. `GET /provider/files/{id}/content` downloads your own file (as an attachment, `nosniff`, `no-store`); someone else's id is a 404. `DELETE /provider/files/{id}` erases it; a **credential** can be deleted only in a draft state (409 `CREDENTIALS_LOCKED` otherwise).

Not exercised live (unit tests only, `tests/api/test_marketplace_providers.py`): the 15 MB boundary (exactly 15 MB accepted, one byte more refused), the per-purpose file quota, avatar replacement, and delete. Their error bodies follow the standard envelope with the codes above.

`upload_credential_201`:
```json
{
  "data": {
    "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
    "purpose": "credential",
    "filename": "bar-licence.pdf",
    "mime": "application/pdf",
    "size_bytes": 38,
    "scan_status": "clean",
    "created_at": "2026-11-05T14:46:59.470952+00:00"
  },
  "meta": null
}
```

`upload_content_mismatch_422` (a Windows executable sent as `application/pdf`):
```json
{
  "error": {
    "code": "FILE_CONTENT_MISMATCH",
    "message": "That file's contents don't match its type, or the file is empty.",
    "field_errors": []
  }
}
```

`upload_unsupported_type_422`:
```json
{
  "error": {
    "code": "UNSUPPORTED_FILE_TYPE",
    "message": "That file type isn't allowed here. Upload a PDF, PNG or JPEG.",
    "field_errors": []
  }
}
```

`download_own_file_headers` (the response is the file bytes, so only the headers are captured):
```json
{
  "status": 200,
  "content_type": "application/pdf",
  "content_disposition": "attachment; filename=\"bar-licence.pdf\"; filename*=UTF-8''bar-licence.pdf",
  "x_content_type_options": "nosniff",
  "cache_control": "private, no-store"
}
```

### Portfolio (`POST /provider/portfolio`, `PATCH`/`DELETE /provider/portfolio/{id}`)

`{"title", "description"?, "url"?, "file_id"?}`: a link (`http://` or `https://` only) or a `portfolio` upload is required; max 12 items. `PATCH` also takes `position`. Items appear in `portfolio` on `GET /provider/me`. Not exercised live beyond the add below; update, delete, the 12-item cap and the "belongs to another expert → 404" rule are unit-tested.

`portfolio_add_201`:
```json
{
  "data": {
    "id": "f9a73205-d1d7-412c-923a-a6269b04516b",
    "title": "Fintech rebrand",
    "description": null,
    "url": "https://portfolio.example.com/fintech",
    "file_id": null,
    "position": 0
  },
  "meta": null
}
```

## Admin endpoints (`/admin/marketplace/*`)

Guard: active staff with TOTP on. Anyone else gets the hidden 404 (`{"detail": "Not Found"}`); staff without the `moderation` scope get 403 `FORBIDDEN`; a Super Admin passes. Decisions below carry `reason` (10-500 chars) and, where marked, `totp_code` (6 digits).

`admin_anonymous_404`:
```json
{
  "detail": "Not Found"
}
```

`admin_wrong_scope_403` (a `support`-only Admin):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### `GET /admin/marketplace/providers/counts`

`admin_counts` (this is a shared e2e database, so counts differ per run):
```json
{
  "data": {
    "by_status": {
      "invited": 0,
      "applied": 0,
      "in_review": 0,
      "changes_requested": 0,
      "approved": 1,
      "suspended": 0,
      "rejected": 1
    },
    "licence_expiring": 0
  },
  "meta": null
}
```

### `GET /admin/marketplace/providers?status&category&query&limit&cursor`

Oldest first (a work queue). `limit` 1-100 (default 25), `cursor` from `meta.next_cursor`. `query` matches name or email. An unknown `status` is a 422. Rows are summaries; open the detail for files and checklist.

`admin_queue_in_review`:
```json
{
  "data": {
    "providers": [
      {
        "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
        "user_id": "b8303f2e-f388-4dfa-b787-eaf636923123",
        "email": "delivered+e2e-84b32daa0122@resend.dev",
        "display_name": "Ngozi Eze",
        "headline": "Corporate lawyer for early-stage startups",
        "category": "Legal",
        "status": "in_review",
        "source": "invite",
        "submitted_at": "2026-11-05T14:46:59.646704+00:00",
        "created_at": "2026-11-05T14:46:58.231582+00:00",
        "invite_pending": false,
        "invite_expires_at": null,
        "licence_expires_on": null,
        "licence_expiring": false,
        "prescreen_flags": [],
        "vetted_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

### `POST /admin/marketplace/providers/invite` (201)

`{"email", "category"?, "display_name"?}`. Email is lower-cased. `undelivered` lists addresses the mail backend refused (the invite still exists; use resend).

`admin_invite_201`:
```json
{
  "data": {
    "provider": {
      "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
      "user_id": null,
      "email": "delivered+e2e-84b32daa0122@resend.dev",
      "display_name": "Ngozi Eze",
      "headline": null,
      "category": "Legal",
      "status": "invited",
      "source": "invite",
      "submitted_at": null,
      "created_at": "2026-11-05T14:46:58.231582+00:00",
      "invite_pending": true,
      "invite_expires_at": "2026-11-19T14:46:58.265094+00:00",
      "licence_expires_on": null,
      "licence_expiring": false,
      "prescreen_flags": [],
      "vetted_at": null
    },
    "undelivered": []
  },
  "meta": null
}
```

`admin_invite_duplicate_409`:
```json
{
  "error": {
    "code": "ALREADY_INVITED",
    "message": "That person already has a pending invitation. Re-send it.",
    "field_errors": []
  }
}
```

`admin_invite_bad_email_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "email",
        "message": "value is not a valid email address: An email address must have an @-sign."
      }
    ]
  }
}
```

`email_invite` (the emailed message; the link is `{APP_BASE_URL}/provider/invite/{token}`):
```json
{
  "to": "delivered+e2e-84b32daa0122@resend.dev",
  "subject": "You're invited to join the Cofoundaz Marketplace",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px\">Join the Cofoundaz Marketplace</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, Cofoundaz has invited you to offer your services as a Legal expert to founders on our Marketplace. Accept the invitation, complete your profile and upload your credentials, and our team will review your application.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/invite/VvAazeusjTa64aXrZDRWCTW5D7l1u42vkQgzroMfBPk\" style=\"display:inline-block;padding:12px 22px;background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Accept invitation</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/invite/VvAazeusjTa64aXrZDRWCTW5D7l1u42vkQgzroMfBPk\" style=\"color:#4f46e5\">http://localhost:3000/provider/invite/VvAazeusjTa64aXrZDRWCTW5D7l1u42vkQgzroMfBPk</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">This invitation expires in 14 days.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-05T14:46:58.273129+00:00"
}
```

### `POST /admin/marketplace/providers/{id}/resend-invite` and `/revoke-invite`

Resend replaces the link and extends the expiry; revoke deletes the pending invite. Both are only valid before the invite is accepted (409 `INVITE_NOT_PENDING`). Revoke answers `{"data": {"revoked": true}, "meta": null}` (read from the route code; the status is unit-tested, the body is **not captured**).

`admin_resend_invite`:
```json
{
  "data": {
    "provider": {
      "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
      "user_id": null,
      "email": "delivered+e2e-84b32daa0122@resend.dev",
      "display_name": "Ngozi Eze",
      "headline": null,
      "category": "Legal",
      "status": "invited",
      "source": "invite",
      "submitted_at": null,
      "created_at": "2026-11-05T14:46:58.231582+00:00",
      "invite_pending": true,
      "invite_expires_at": "2026-11-19T14:46:58.364623+00:00",
      "licence_expires_on": null,
      "licence_expiring": false,
      "prescreen_flags": [],
      "vetted_at": null
    },
    "undelivered": []
  },
  "meta": null
}
```

### `GET /admin/marketplace/providers/{id}`

The full application. Notes for the FE:

- `checks` is the stored checklist (who ticked and when, notes); `required_checks` and `references_required` say what this category needs; `missing_checks` is what still blocks approval.
- `badge` is what the **public** sees (empty until approved); `badge_preview` is what the tooltip would say if approved now.
- `prescreen_flags`: deterministic chips (`contact_details`, `guaranteed_outcome`, `prohibited_content`, each with the `field`). They never block; they tell the reviewer where to look.
- `licence_expiring` is true from 30 days before expiry (approved or suspended experts).
- `files` are metadata; read bytes with the audited file route below. `history` is the audit trail for this expert, newest first, max 50.

`admin_provider_detail_before_vetting` (in review, nothing ticked):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "user_id": "b8303f2e-f388-4dfa-b787-eaf636923123",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "category": "Legal",
    "status": "in_review",
    "source": "invite",
    "submitted_at": "2026-11-05T14:46:59.646704+00:00",
    "created_at": "2026-11-05T14:46:58.231582+00:00",
    "invite_pending": false,
    "invite_expires_at": null,
    "licence_expires_on": null,
    "licence_expiring": false,
    "prescreen_flags": [],
    "vetted_at": null,
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "terms_version": "draft-2026-11",
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "review_message": null,
    "suspended_reason": null,
    "commission_bps": null,
    "required_checks": [
      "id",
      "licence",
      "references"
    ],
    "references_required": 2,
    "missing_checks": [
      "id",
      "licence",
      "references"
    ],
    "checks": {},
    "badge": {
      "vetted": false,
      "checks": []
    },
    "badge_preview": [],
    "portfolio": [],
    "files": [
      {
        "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
        "purpose": "credential",
        "filename": "bar-licence.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-05T14:46:59.470952+00:00"
      }
    ],
    "history": [
      {
        "action": "marketplace.provider.submitted",
        "at": "2026-11-05T14:46:59.602088+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.profile_updated",
        "at": "2026-11-05T14:46:59.359143+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invite_accepted",
        "at": "2026-11-05T14:46:59.142907+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invite_resent",
        "at": "2026-11-05T14:46:58.322496+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invited",
        "at": "2026-11-05T14:46:58.231582+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      }
    ]
  },
  "meta": null
}
```

`admin_provider_detail_approved`:
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "user_id": "b8303f2e-f388-4dfa-b787-eaf636923123",
    "email": "delivered+e2e-84b32daa0122@resend.dev",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "category": "Legal",
    "status": "approved",
    "source": "invite",
    "submitted_at": "2026-11-05T14:47:00.253702+00:00",
    "created_at": "2026-11-05T14:46:58.231582+00:00",
    "invite_pending": false,
    "invite_expires_at": null,
    "licence_expires_on": "2027-11-05",
    "licence_expiring": false,
    "prescreen_flags": [],
    "vetted_at": "2026-11-05T14:47:00.660435+00:00",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "timezone": "Africa/Lagos",
    "location": "Lagos, Nigeria",
    "terms_version": "draft-2026-11",
    "terms_accepted_at": "2026-11-05T14:46:59.175493+00:00",
    "review_message": null,
    "suspended_reason": null,
    "commission_bps": null,
    "required_checks": [
      "id",
      "licence",
      "references"
    ],
    "references_required": 2,
    "missing_checks": [],
    "checks": {
      "id": {
        "at": "2026-11-05T14:46:59.951595+00:00",
        "by": "d2095386-bf0a-48aa-8095-32f75238c184",
        "done": true,
        "note": "NIN slip seen"
      },
      "licence": {
        "at": "2026-11-05T14:46:59.951595+00:00",
        "by": "d2095386-bf0a-48aa-8095-32f75238c184",
        "done": true,
        "note": "Bar roll number matched",
        "expires_on": "2027-11-05"
      },
      "references": {
        "at": "2026-11-05T14:46:59.951595+00:00",
        "by": "d2095386-bf0a-48aa-8095-32f75238c184",
        "done": true,
        "note": "Two founders emailed",
        "count": 2,
        "updated_at": "2026-11-05T14:47:00.328879+00:00",
        "updated_by": "d2095386-bf0a-48aa-8095-32f75238c184"
      }
    },
    "badge": {
      "vetted": true,
      "checks": [
        "Identity verified",
        "Professional licence verified",
        "2 client references checked"
      ]
    },
    "badge_preview": [
      "Identity verified",
      "Professional licence verified",
      "2 client references checked"
    ],
    "portfolio": [],
    "files": [
      {
        "id": "8788281e-402f-4ff7-b29e-c0179256cf28",
        "purpose": "credential",
        "filename": "bar-licence.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-05T14:46:59.470952+00:00"
      }
    ],
    "history": [
      {
        "action": "marketplace.provider.unsuspended",
        "at": "2026-11-05T14:48:30.736556+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "Checked the documents against the rubric"
      },
      {
        "action": "marketplace.provider.suspended",
        "at": "2026-11-05T14:48:00.292561+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "Checked the documents against the rubric"
      },
      {
        "action": "marketplace.provider.approved",
        "at": "2026-11-05T14:47:00.554634+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "Checked the documents against the rubric"
      },
      {
        "action": "marketplace.provider.vetting_updated",
        "at": "2026-11-05T14:47:00.291011+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.submitted",
        "at": "2026-11-05T14:47:00.200795+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.changes_requested",
        "at": "2026-11-05T14:46:59.992842+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "The licence scan is blurry; please upload a clearer copy."
      },
      {
        "action": "marketplace.provider.vetting_updated",
        "at": "2026-11-05T14:46:59.923074+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.submitted",
        "at": "2026-11-05T14:46:59.602088+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.profile_updated",
        "at": "2026-11-05T14:46:59.359143+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invite_accepted",
        "at": "2026-11-05T14:46:59.142907+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invite_resent",
        "at": "2026-11-05T14:46:58.322496+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invited",
        "at": "2026-11-05T14:46:58.231582+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      }
    ]
  },
  "meta": null
}
```

`admin_provider_detail_history` (the `history` array):
```json
{
  "data": {
    "history": [
      {
        "action": "marketplace.provider.unsuspended",
        "at": "2026-11-05T14:48:30.736556+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "Checked the documents against the rubric"
      },
      {
        "action": "marketplace.provider.suspended",
        "at": "2026-11-05T14:48:00.292561+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "Checked the documents against the rubric"
      },
      {
        "action": "marketplace.provider.approved",
        "at": "2026-11-05T14:47:00.554634+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "Checked the documents against the rubric"
      },
      {
        "action": "marketplace.provider.vetting_updated",
        "at": "2026-11-05T14:47:00.291011+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.submitted",
        "at": "2026-11-05T14:47:00.200795+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.changes_requested",
        "at": "2026-11-05T14:46:59.992842+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": "The licence scan is blurry; please upload a clearer copy."
      },
      {
        "action": "marketplace.provider.vetting_updated",
        "at": "2026-11-05T14:46:59.923074+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.submitted",
        "at": "2026-11-05T14:46:59.602088+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.profile_updated",
        "at": "2026-11-05T14:46:59.359143+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invite_accepted",
        "at": "2026-11-05T14:46:59.142907+00:00",
        "actor_email": "delivered+e2e-84b32daa0122@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invite_resent",
        "at": "2026-11-05T14:46:58.322496+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.provider.invited",
        "at": "2026-11-05T14:46:58.231582+00:00",
        "actor_email": "delivered+e2e-53becda216e7@resend.dev",
        "reason": null
      }
    ]
  }
}
```

### `GET /admin/marketplace/providers/{id}/files/{file_id}`

Returns the file bytes (attachment, `nosniff`, `no-store`). **Every read writes an audit row (`marketplace.file.viewed`)**, so link to it from a button, never prefetch it. A file that is not that expert's, or was erased, is a 404.

`admin_read_credential_headers`:
```json
{
  "status": 200,
  "content_type": "application/pdf",
  "content_disposition": "attachment; filename=\"bar-licence.pdf\"; filename*=UTF-8''bar-licence.pdf"
}
```

`admin_read_erased_file_404` (after a reject):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `PUT /admin/marketplace/providers/{id}/vetting`

Tick or untick checks; allowed in `in_review`, `changes_requested`, `approved` and `suspended`. Body `{"checks": {"<key>": {"done": bool, "note"?, "expires_on"?, "count"?}}}` with keys `id`, `licence`, `references`, `portfolio`. `licence` needs `expires_on` in the future; `references` needs `count` 1+; a key not required for the category (for example `portfolio` for Legal) is a 422. Returns the detail.

`admin_vetting_licence_needs_expiry_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Enter a licence expiry date in the future.",
    "field_errors": []
  }
}
```

`admin_vetting_check_not_applicable_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The 'portfolio' check doesn't apply to Legal.",
    "field_errors": []
  }
}
```

`admin_vetting_put_one_reference_200` (the response is the full detail, shown trimmed to the checklist fields; nothing else is changed):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "status": "in_review",
    "missing_checks": [
      "references"
    ],
    "checks": {
      "id": {
        "done": true,
        "note": "NIN slip seen",
        "by": "d2095386-bf0a-48aa-8095-32f75238c184",
        "at": "2026-11-05T14:46:59.951595+00:00"
      },
      "licence": {
        "done": true,
        "note": "Bar roll number matched",
        "by": "d2095386-bf0a-48aa-8095-32f75238c184",
        "at": "2026-11-05T14:46:59.951595+00:00",
        "expires_on": "2027-11-05"
      },
      "references": {
        "done": true,
        "note": null,
        "by": "d2095386-bf0a-48aa-8095-32f75238c184",
        "at": "2026-11-05T14:46:59.951595+00:00",
        "count": 1
      }
    },
    "badge_preview": [
      "Identity verified",
      "Professional licence verified",
      "1 client reference checked"
    ],
    "licence_expires_on": "2027-11-05"
  }
}
```

### Decisions

`POST …/approve` `{"reason", "totp_code"}`; `POST …/reject` `{"template", "message"?, "reason", "totp_code"}` with `template` one of `incomplete_documents`, `licence_not_verified`, `references_not_verified`, `outside_scope`, `other`; `POST …/request-changes` `{"message"}` (10-1000 chars, emailed to the expert and shown to them as `review_message`); `POST …/suspend` and `…/unsuspend` `{"reason", "totp_code"}`. Each returns the detail. An unknown id is a 404 **before** the authenticator code is checked, so a typo never burns a code.

`admin_approve_incomplete_422`:
```json
{
  "error": {
    "code": "VETTING_INCOMPLETE",
    "message": "Tick every check this category needs before approving: id, licence, references.",
    "field_errors": []
  }
}
```

`admin_approve_reason_too_short_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "reason",
        "message": "String should have at least 10 characters"
      }
    ]
  }
}
```

`admin_approve_bad_code_403`:
```json
{
  "error": {
    "code": "STEP_UP_INVALID",
    "message": "That authenticator code isn't right, or was already used. Enter a new one.",
    "field_errors": []
  }
}
```

`admin_request_changes_200` (trimmed to the fields that changed; the body is the full detail):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "status": "changes_requested",
    "review_message": "The licence scan is blurry; please upload a clearer copy."
  }
}
```

`email_changes_requested`:
```json
{
  "to": "delivered+e2e-84b32daa0122@resend.dev",
  "subject": "Changes needed on your Marketplace application",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px\">A few changes are needed</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, our reviewer asked for some changes before we can approve your profile: <br><br><em>The licence scan is blurry; please upload a clearer copy.</em></p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider\" style=\"display:inline-block;padding:12px 22px;background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Update your application</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider\" style=\"color:#4f46e5\">http://localhost:3000/provider</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">Update your profile or documents, then submit again.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-05T14:47:00.084768+00:00"
}
```

`admin_approve_200` (trimmed to the fields that changed):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "status": "approved",
    "vetted_at": "2026-11-05T14:47:00.660435+00:00",
    "badge": {
      "vetted": true,
      "checks": [
        "Identity verified",
        "Professional licence verified",
        "2 client references checked"
      ]
    },
    "review_message": null
  }
}
```

`admin_approve_twice_409`:
```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Can't do that while the expert is 'approved'.",
    "field_errors": []
  }
}
```

`admin_suspend_200` (trimmed):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "status": "suspended",
    "suspended_reason": "Checked the documents against the rubric"
  }
}
```

`admin_unsuspend_200` (trimmed):
```json
{
  "data": {
    "id": "a1ba5716-b147-4f4c-b78c-69c05d952f31",
    "status": "approved",
    "suspended_reason": null
  }
}
```

`admin_reject_bad_template_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "template",
        "message": "Input should be 'incomplete_documents', 'licence_not_verified', 'references_not_verified', 'outside_scope' or 'other'"
      }
    ]
  }
}
```

`admin_reject_200` (trimmed; note `files` is empty because every upload was erased):
```json
{
  "data": {
    "id": "d46ab90c-3040-41a0-bc3a-89fa65a62b3e",
    "status": "rejected",
    "review_message": "The ID scan is unreadable.",
    "files": []
  }
}
```

`email_rejected`:
```json
{
  "to": "delivered+e2e-855ae9012707@resend.dev",
  "subject": "Your Marketplace application",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px\">We can't approve your application</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Tolu Ade, after review we aren't able to approve your Marketplace application. Reason: Your documents were incomplete or unreadable.<br><br><em>The ID scan is unreadable.</em> Uploaded credential documents have been deleted.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/support\" style=\"display:inline-block;padding:12px 22px;background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Contact support</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/support\" style=\"color:#4f46e5\">http://localhost:3000/support</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">If you think this is a mistake, contact support.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-05T14:49:31.048188+00:00"
}
```

## Polling and UX instructions

- **Expert portal after submit:** the expert waits. Poll `GET /provider/status` every 60 s while the portal is open, or re-fetch on focus; there is no push. When `status` becomes `changes_requested`, show `review_message` prominently and re-enable the form; when `approved`, show the "Vetted" badge from `badge`; when `rejected`, show `review_message` (already the template wording unless staff wrote one) and tell them their documents were deleted.
- **Surface the lock.** `in_review` is read-only by design (evidence cannot change mid-review). Disable inputs and say "Under review" instead of letting the expert hit `PROFILE_LOCKED`.
- **Reject is destructive.** The Admin reject dialog must say: "This deletes the applicant's uploaded documents and emails them." and require the reason and authenticator code.
- **Credential files are sensitive.** Open them only on an explicit click (each open is audited and the page should say so).
- **Do not show a Vetted badge** from `status` alone; render only what `badge` returns.
- **Category lock after approval** is permanent from the expert's side: disable the category field and point to support.
- **Invite expiry:** 14 days. An expired or used link is the same 404 on preview and `TOKEN_INVALID` on accept; show "This invitation is no longer valid. Ask for a new one."
- **Suspension** keeps the expert signed in and able to read their record, but nothing is editable.

## Verification table

| Behaviour | Verified live (`e2e/test_marketplace_providers.py`) | Unit test only | Not exercised |
|---|---|---|---|
| Invite, email, preview, accept, replay 400, wrong-account 403 | yes | yes | |
| Resend replaces link (old link 404) | yes | yes | |
| Revoke invite | | yes | |
| Invite expiry | | yes (expiry forced in the DB) | real 14-day wait |
| Daily invite cap (50) | | yes (cap lowered) | |
| Open applications: closed answer | yes | yes | |
| Open applications: success, duplicates, `INVITE_PENDING` | | yes | live with the flag on |
| Profile patch, validation, locks (review, category, suspended) | yes | yes | |
| Pre-screen flags | | yes | |
| Credential upload, sniff, bad type, download headers | yes | yes | |
| 15 MB cap boundary, quota, avatar replace, delete | | yes | |
| Portfolio add | yes | yes (update, delete, limits, ownership) | |
| Submit blockers and submit | yes | yes | |
| Checklist rules, approve gate, step-up code, reason length | yes | yes | |
| Request changes, resubmit, email | yes | yes | |
| Approve, suspend, unsuspend, reject (files erased) | yes | yes | |
| State-machine refusals (`INVALID_STATE`) | yes (approve twice) | yes (parametrised) | |
| Credential read audited; cross-expert file → 404 | yes (read, erased 404) | yes | |
| Admin role gates (404 / 403 / pass) on every route | yes (anonymous, wrong scope) | yes (every route) | |
| Pagination cursor, filters, counts | counts and filter yes | yes (cursor) | |
| Licence-expiry flag | | yes | live over 30 days |
| Real email delivery (Resend / SMTP) | | | yes (the e2e server writes mail to files; staging not exercised) |
| Real virus scanner | | | yes (the scanner is the Documents placeholder, which marks files clean after the byte check) |
