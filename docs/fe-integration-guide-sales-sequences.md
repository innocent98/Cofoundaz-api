# FE integration guide — Sales sequences and outreach (Module 11, slice B)

Every body below is copied from `e2e/_captures/sales_b/*.json`, captured by `e2e/test_sales_b.py` against a real
uvicorn + Postgres. Ids/emails are throwaway e2e values. Base: `/api/v1`. Auth: bearer + `X-Workspace-Id`.
Envelope: `{data, meta}` on success, `{error: {code, message, field_errors}}` on failure.
Access: **founder + team member** for everything under `/sales/*`; mentor/consultant/others get `403`
(`sequences_mentor_403`), another workspace's ids are `404` (`sequence_cross_workspace_404`).
`POST /sales/sending/resume` is **founder only** (`resume_forbidden_team_member`: team member gets 403).

## 1. What this is
Members build a sequence (1 to 10 steps), enroll leads/contacts, and the server sends each step by email from the
company outreach mailbox. Replies are **manual**: the member presses *Mark replied*. Bounces and unsubscribes stop a
contact automatically.

## 2. The "sending isn't set up" state (show this first)
`GET /sales/sending` (`sending_not_configured`, captured on the live server whose mailbox env is unset):
```json
{
  "data": {
    "configured": false,
    "reason": "not_configured",
    "domain": null,
    "sent_today": 0,
    "daily_cap": 100,
    "hourly_cap": null,
    "paused": false,
    "paused_reason": null,
    "paused_at": null
  },
  "meta": null
}
```
While `configured:false`: sequences can be built, saved and enrolled; **nothing is sent**. Show "Sending isn't set up yet".
The state disappears by itself when the env is set; no FE change. Configured shape
(`sending_configured_in_process`, **captured in-process, not on the live server**, because the live e2e server is unset by design):
```json
{
  "data": {
    "configured": true,
    "reason": null,
    "domain": "mail.example.test",
    "sent_today": 4,
    "daily_cap": 100,
    "hourly_cap": 30,
    "paused": false,
    "paused_reason": null,
    "paused_at": null
  },
  "meta": null
}
```
`paused:true` with `paused_reason:"bounces"` (`sending_paused`; note this live capture also shows `configured:false`)
means a bounce spike paused the whole workspace: show a banner; founders get a **Resume** button
(`POST /sales/sending/resume` returns this same shape with `paused:false`).
Caps to surface: `daily_cap` 100 per workspace per UTC day vs `sent_today`; `hourly_cap` is global to the mailbox.

## 3. Sequences
- `POST /sales/sequences` (201) body `{name, steps:[{wait_days 0..60, subject, body}], stop_on_stage_change}` → (`sequence_created`)
```json
{
  "data": {
    "id": "482c8701-2c1d-460f-99ca-4bf6e325053d",
    "name": "Cold intro",
    "steps": [
      {
        "body": "Hi {{first_name}},\n\nWe help teams like {{company}} get paid faster.\n\n{{sender_name}}",
        "subject": "Quick idea for {{company}}",
        "wait_days": 0
      },
      {
        "body": "Hi {{first_name}}, bumping this in case it got buried.\n\n{{sender_name}}",
        "subject": "Re: Quick idea for {{company}}",
        "wait_days": 3
      }
    ],
    "step_count": 2,
    "stop_on_stage_change": false,
    "status": "active",
    "active_enrollments": 0,
    "total_enrollments": 0,
    "reply_rate": 0.0,
    "created_at": "2026-10-22T13:00:18.371790+00:00",
    "updated_at": "2026-10-22T13:00:18.371790+00:00"
  },
  "meta": null
}
```
- `GET /sales/sequences?include_archived&limit&offset` → `{items,total,limit,offset}` (`sequences_list`). `reply_rate` is replied/total enrollments (0..1).
- `GET|PATCH /sales/sequences/{id}`; PATCH accepts `name, steps, stop_on_stage_change, status` (`active|paused`). `status:"archived"` via PATCH is 422; use `DELETE` (stops live enrollments; `sequence_archived`).
- Merge tags: `{{first_name}} {{last_name}} {{company}} {{sender_name}}`. Unknown tag → 422 (`sequence_error_unknown_merge_tag`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Unknown merge tag {{nope}}. Use first_name, last_name, company, sender_name.",
    "field_errors": [
      {
        "field": "steps.0.subject",
        "message": "Unknown merge tag {{nope}}. Use first_name, last_name, company, sender_name."
      }
    ]
  }
}
```
  `field` is `steps.<i>.subject|body`. Empty `steps` is also 422 (`sequence_error_no_steps`). **UX trap:** a missing value renders as an empty string, so
  "Quick idea for {{company}}" for a contact without an account is sent as "Quick idea for" (see `delivered_message_headers`). Warn the member in the builder preview.
- Editing steps affects only sends not yet made; `current_step` of running enrollments is kept.

## 4. Enrolling
`POST /sales/sequences/{id}/enroll` body `{lead_ids?, contact_ids?}` (at least one) → `{enrolled: <count>, enrollments: [...], skipped: [{id, reason}]}` (`enroll_with_skips`, trimmed):
```json
[
  {
    "id": "5ee5ab95-4a55-475f-b8af-1a57a5fe8098",
    "reason": "no_email"
  }
]
```
Reasons: `no_email`, `suppressed` (unsubscribed/bounced, `enroll_suppressed`), `already_active` (`enroll_already_active`), `converted` (lead already became a contact: enroll the contact), `not_found` (also another workspace's id).
The sender is the **enrolling member** (`sender_name`/`sender_email` frozen on the enrollment; Reply-To is that email).
**Trap (timestamps):** enrollments use `...Z` (`next_send_at`, `created_at`); sequences/drafts use `+00:00`. Parse both.

`GET /sales/sequences/{id}/enrollments?status=&limit&offset` → `{items,total,limit,offset}`. Statuses: `active, completed, replied, unsubscribed, bounced, stopped`.
`stopped_reason` values seen/possible: `marked_replied, stopped_by_member, sequence_archived, deal_stage_changed, send_failed, no_sender, finished, unsubscribed, bounced`.
(`enrollments_after_first_send`: all `current_step:1`; `enrollments_filtered_bounced`: the bounced contact is listed with `status:"bounced"`, `next_send_at:null`)
- `POST /sales/enrollments/{id}/replied` and `/stop` → the enrollment (`enrollment_replied`, `enrollment_stopped`). Already ended → **409** (`enrollment_error_not_active`):
```json
{
  "error": {
    "code": "ENROLLMENT_NOT_ACTIVE",
    "message": "This enrollment has already ended.",
    "field_errors": []
  }
}
```
"Stop when they reply" is manual. Show *Mark replied* on active rows.

## 5. What the recipient gets (`delivered_message_headers`, from a real SMTP dialogue to a fake server)
```json
{
  "note": "Headers of the message the fake SMTP server received from SmtpOutreachSender; recipient is a placeholder",
  "From": "delivered+team_member-5c02089ea29f at NovaPay <outreach@mail.example.test>",
  "To": "ada-PLACEHOLDER@prospect.example",
  "Reply-To": "<the sending member's account email>",
  "Subject": "Quick idea for",
  "Message-ID": "<send-id@mail.example.test>",
  "X-Cofoundaz-Step": "<send-id>",
  "List-Unsubscribe": "<http://127.0.0.1:8010/api/v1/public/sales/unsubscribe/<signed-token>>",
  "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
  "body_text": "Hi Ada,\r\n\r\nWe help teams like  get paid faster.\r\n\r\ndelivered+team_member-5c02089ea29f\r\n\r\n--\r\nNovaPay\r\nUnsubscribe: http://localhost:3000/unsubscribe/<signed-token>\r\n"
}
```
Footer (not editable): startup name, optional postal address (`PATCH /sales/settings` `outreach_postal_address`), unsubscribe link to the **FE page** `{APP_BASE_URL}/unsubscribe/{token}`.

## 6. Public unsubscribe page (`/unsubscribe/[token]`, no auth, rate limit 30/min per IP)
`GET /public/sales/unsubscribe/{token}` (`public_unsubscribe_status`) then `POST` (`public_unsubscribe_post`):
```json
{
  "data": {
    "email": "a***@prospect.example",
    "sender": "NovaPay",
    "unsubscribed": false
  },
  "meta": null
}
{
  "data": {
    "email": "a***@prospect.example",
    "sender": "NovaPay",
    "unsubscribed": true
  },
  "meta": null
}
```
The email is masked. POST is idempotent. Bad/forged token → 404 (`public_unsubscribe_bad_token`):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
A browser/mail client opening the API GET URL with `Accept: text/html` is `303`-redirected to this FE page, so always call the API with `Accept: application/json` (or `*/*`). Page flow: GET on load (if `unsubscribed:true` show "You're already unsubscribed"), button → POST, then "You won't hear from {sender} again". The POST must not require a body (mail clients one-click it with a form body).

## 7. "Write with AI" and deal follow-up
Step draft: `POST /sales/sequences/{id}/steps/{i}/write` body `{instructions?}` → **202** (`draft_requested_202`), poll `GET /sales/drafts/{id}` until `status` is `ready|failed` (`draft_ready`; the stub LLM wording is placeholder text). It appends nothing: drop `subject`/`body` into the step yourself. `failed` has `error` (`over_budget|generation_failed|bad_output|target_missing`).
Follow-up: `POST /sales/deals/{id}/follow-up` (202, `follow_up_requested_202`; second call while generating → 409 `DRAFT_IN_PROGRESS`), `GET /sales/deals/{id}/follow-up` → latest draft or `null` (`follow_up_latest_ready`).
`POST /sales/deals/{id}/follow-up/send` body `{draft_id, subject, body}` sends **once** to the deal contact. Errors: `SENDING_NOT_CONFIGURED` 409 (live: `follow_up_send_not_configured`), `SENDING_PAUSED` 409, `RECIPIENT_SUPPRESSED` 409, `DRAFT_NOT_READY` 409, `DRAFT_ALREADY_SENT` 409, `SEND_LIMIT_REACHED` 429 (hourly mailbox cap, 100/day, or 1 per contact per day), `SEND_FAILED` 503 (draft stays sendable), `SEND_UNCERTAIN` 503 (the mail may have been delivered: tell the member to check their mailbox/the contact before trying again; the draft stays marked sent; **unit-tested only**), `RECIPIENT_REJECTED` 422, 422 if the contact has no email.
```json
{
  "error": {
    "code": "SENDING_NOT_CONFIGURED",
    "message": "Sending isn't set up yet.",
    "field_errors": []
  }
}
```
Success body: `{sent:true, send_id, to, draft_id}` (unit-test shape only; **not captured live**, since the live server is unconfigured).

## 8. UX consequences to surface
- Sending is paused workspace-wide after a bounce spike (>10% of the last 50, min 10 sends): banner + founder Resume. Founders also get a notification "Sales emails paused: too many bounces" (CTA Review → `/sales/sequences`).
- A contact who unsubscribes or bounces is removed from **every** sequence in the workspace and can't be re-enrolled (`suppressed`).
- A step can be delayed a day by caps without any error; `next_send_at` simply moves. Never show "failed" for that.
- Stop-on-stage-change sequences stop when the contact's deal changes stage (`stopped_reason: deal_stage_changed`). Converting a lead keeps its running sequence.

## 9. Verification table
| Section | Status |
|---|---|
| Sequence CRUD, validation errors, list | verified live |
| Enroll + skip reasons, replied/stop/409, cross-workspace 404, mentor 403, founder-only resume | verified live |
| Not-configured `GET /sending`, follow-up send 409 not-configured | verified live |
| Real SMTP dialogue, headers, footer, step advance | verified live against a **fake SMTP server** (real `SmtpOutreachSender`), not a real mailbox |
| Public unsubscribe GET/POST/forged token, suppression on re-enroll | verified live, with the real token from the sent message |
| Configured `GET /sending` | captured **in-process** (TestClient on the same DB), not on the live server |
| Bounce intake | **synthetic DSN** through a stub IMAP mailbox in-process; parsing/idempotency proven, real IMAP and real DSN wording **not verified live** |
| Delivery via the real cPanel mailbox (TLS, SPF/DKIM/DMARC) | **not verified live** |
| Follow-up send success body, caps (`SEND_LIMIT_REACHED`), auto-pause | unit tests only |
| Draft content | stub LLM wording; real model not checked |
