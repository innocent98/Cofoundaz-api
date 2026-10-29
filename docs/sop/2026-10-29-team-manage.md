# SOP: Team page BE (light): members, invitations, audit, comments index

**What shipped:** change a member's role, remove a member, leave a workspace, list / resend / revoke invitations, a founder-only Audit tab (+ CSV), the
Comments-tab thread index, and Documents-grant revocation on removal or leave (handoff 0b-41). **No migration.** Branch `feature/team-be-gaps`.
Gap list `cofoundaz/docs/checklist/team-prd-gaps.md`; guide `docs/fe-integration-guide-team.md` (live-captured).

## Why

The Team page had invitations, a member list and comments only. A person could never be removed, a role never changed, a wrong invitation never
revoked, and a Documents grant outlived the membership it was issued for.

## How: decisions and rejected alternatives

| Topic | Decision |
|---|---|
| **Scope (Adebayo, 2026-10-29)** | "As simple and light as it can be; override the PRD." Designed screens + the required list only. Not built: Team Tasks (PRD 23.1), Directory (23.5), per-module grant matrix (23.4). |
| **Removal** | `memberships.status = removed` (the row stays, so history stays attributed). `require_workspace` resolves only active memberships, so every route and the SSE stream (which re-checks on a timer) lock the person out at once. No sessions are revoked: the account is still valid for its other workspaces. Rejected: deleting the row (breaks authorship and the unique key). |
| **Grants, links, invitations** | In the same transaction: Documents grants revoked (`revoked_at` + `revoked_by_id`, history kept; matched by account OR by the email addressed), public share links the person created revoked, pending invitations they sent revoked (Opus review: a removed founder could otherwise still bring people in). Open signature requests they created are untouched (follow-up). Demoting a founder also revokes the invitations they sent. |
| **Founders** | Founder-only management. A founder cannot change their own role, remove themselves, or leave as the last active founder. Role changes, removals and leaving all take a row lock on the workspace and re-check under it that the actor is still an active founder, so two founders acting on each other at once cannot leave the workspace with none (Opus review). |
| **Roles** | Any of the invite roles except investor. The audit log records that a role changed, not to what (`audit_log` has no payload column; adding one was out of scope). |
| **Re-invite** | Fixed an existing bug: accepting an invitation as a previously removed member succeeded but left the membership removed. It now reopens the row with the invited role. A suspended member is not reopened (admin decision). |
| **Invitations** | Resend replaces the token hash (old link dies) and sets 14 days; the email is sent after commit; failure is reported (`delivered: false`), not raised. Resend revives an expired invitation. Revoke sets `revoked`. |
| **Audit tab** | Reads `audit_log` rows of this workspace whose action starts with `team.` or `workspace.` (an allow-list: Journal and other private actions never appear). New rows: `team.invitation.created/resent/revoked`, `team.member.joined/role_changed/removed/left`. Cursor is (time, id): rows written in one transaction (a batch invite) share a timestamp, so time alone skipped rows at page boundaries. CSV neutralises formula-leading cells (also after whitespace or control characters; tab and CR count). `last_active_at` on the members list is founders-only: it is an account-wide sign-in time (reviews). |
| **Comments index** | Computed from `comments` (no new table): threads grouped by entity and section, limited to threads the caller wrote in or was mentioned in, from the newest 500 comments. |

## What's involved

`app/services/team/members.py` (new), `app/api/v1/endpoints/team.py`, `app/schemas/team.py`, `app/services/onboarding/invites.py` (audit rows, re-invite fix).
Tests `tests/api/test_team_manage.py` (25). Live `e2e/test_team_manage.py`, captures `e2e/_captures/team_manage/`.

## Verification

21 new tests plus the team, invitation, onboarding, notification, roadmap and journal suites green on fresh scratch DBs; black, isort, ruff, mypy clean; no migration.
Live e2e journey and the existing `test_team_invitations` journey passed against a real server.

## Operate / roll back

No env vars, no migration: revert the code. Memberships marked removed stay removed on revert (the old code also treats non-active as no access).

## Follow-ups

Team Tasks with Roadmap sync and AI triage; Directory; grants matrix per module; "to what" in role-change audit rows; seat counting for Billing; a notification to
the removed or re-roled member; revoking share links and signature requests a removed member created.
