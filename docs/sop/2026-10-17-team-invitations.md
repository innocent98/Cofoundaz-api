# SOP — "+ Invite" at any time (`POST /team/invitations`) + a real invite link

**What shipped (one line):** Founders can invite members after onboarding through `POST /team/invitations` (the
top bar's "+ Invite", PRD §2.1 / §23), and the invitation email finally carries a clickable `/invite/{token}`
link. Branch `feat/workspace-invites` → PR to `develop` (independent of #129/#130).

## Why
Dashboard gap #7 (`cofoundaz/docs/checklist/dashboard-prd-gaps.md`): "+ Invite" said "coming soon". The only
invite endpoint was onboarding's, which 409s once onboarding is complete. Found along the way: the invite email
contained **only a raw token and no link**, so no invitee could actually accept from the email.

## How (key decisions)
| Decision | Why | Rejected |
|---|---|---|
| New `POST /team/invitations` (PRD's name), founder only, reusing `create_invitations` | One set of skip rules (member / pending / duplicate) for onboarding and later invites | A second invite implementation |
| Roles per the PRD modal; "Co-Founder" → `founder`; `investor` not invitable (422) | Matches PRD 01.6 step 5. Investors arrive via Funding | Free-form roles |
| Email via the shared rendered-email helper, linking to the FE `/invite/{token}`; workspace/inviter names HTML-escaped | Same look as verify/reset. Names are user-entered | Raw `<code>` token |
| **Abuse guards** (from the commit security review): `@limiter.limit("10/minute")` per user + **50 invitations / workspace / 24h** in `create_invitations` (429 `INVITE_LIMIT_REACHED`) | A founder account must not become a phishing relay signed as Cofoundaz. The cap covers onboarding too | Relying on the global 120/min limit |
| Batch of 1 to 20 rows (onboarding too) + `10/minute` on `/onboarding/invites` | Bounded fan-out per request | — |
| Cap check serialized per workspace (`SELECT … FOR UPDATE` on the startup row) | Concurrent requests could each pass the count (TOCTOU, flagged by the security review) | Redis counter (extra moving part) |
| **Send after commit**: `create_invitations` returns an `InviteBatch` outbox, the endpoint commits, then `deliver_invitations` sends; a failed recipient goes in `undelivered` | Before this, an email could go out for an invitation that then rolled back, letting a failing batch slip past the cap (security review). Raw tokens stay out of the jobs table | Email jobs carrying raw tokens at rest |

## What's involved
`app/api/v1/endpoints/team.py` (new), `app/schemas/team.py` (new), `app/api/v1/api.py` (mount `/team`),
`app/services/auth/emails.py` (`invitation_email`), `app/services/onboarding/invites.py` (uses it; daily cap),
`e2e/conftest.py` (mailbox reads `/invite/` links), `e2e/test_team_invitations.py` (new), tests
`tests/api/test_team_invitations.py` (new) + `tests/api/onboarding/test_invites.py` (link-based token),
guide `docs/fe-integration-guide-team-invitations.md`. No migration.

## Verification
- pytest **2184 passed on a fresh DB, 97.42%** (the CI-equivalent run). black/isort/ruff/mypy clean, pylint 9.88.
- `scripts/e2e_run.sh` **67 passed** (new invite journey; onboarding/roadmap invite journeys now use the link).
- Live on local docker: invite → email with a working link → preview → signup → accept → mentor sees the dashboard
  restricted, mentor's own invite 403, founder notified "A new member joined your workspace".

## Operate / roll back
No migration, no config. Revert the PR to roll back (the email goes back to token-only).

## Follow-ups
- Team page (PRD 23.4): list members/invitations, resend/revoke, role change, remove. That's the Team page slice.
- Invitee without a profile name shows `first_name: ""` in the dashboard greeting (FE must handle it; noted in the guide).
