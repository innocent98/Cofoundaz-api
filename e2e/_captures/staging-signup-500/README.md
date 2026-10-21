# Captures: signup / resend / forgot when the email provider refuses the send

Captured on 2026-10-21 against a local uvicorn running branch `fix/staging-signup-500`, with
`EMAIL_BACKEND=resend` and a deliberately invalid `RESEND_API_KEY`, so the real Resend API
answered 401 for every send. The isolated DB `signup500_capture` was migrated to head, then dropped.

- `*.status` is the HTTP status, `*.response.json` the verbatim body, `*.request.json` the body sent.
- All emails are placeholders (`*-placeholder@resend.dev`). The signup password in the request
  files is redacted. It was a throwaway value, removed so the secret scanners stay quiet.
- After the run, `users` had no row for `new-placeholder@resend.dev` and `auth_tokens` was empty.
  So a failed signup creates no account, and a failed resend or forgot leaves no token.
