# SOP: member e-mail leaks (business suggestions, calendar attendees)

**What shipped:** two more places that gave member e-mail addresses to roles other than founder and team member now return `null` (Adebayo, 2026-11-12: "Founder + TM only"). Follows API #240 (`GET /team/members`). Branch `fix/member-email-leaks`. No migration.

| Endpoint | Before | Now |
|---|---|---|
| `GET/POST /business-builder/suggestions`, approve, reject (`serialize_suggestion`) | `author.email` for every role | `author.email` only for founder and team member (`include_email`); others get the name |
| `GET /calendar/meetings`, `/{id}` (`serialize_meeting`) | `attendees[].email` for every invited role | `null` unless founder or team member; internal attendee `name` falls back to the profile name |

**Tests:** `tests/api/test_business_suggestion_email.py` (per role), `tests/api/test_calendar_meetings.py` (per role + name fallback); e2e journeys updated (`test_business_builder.py`, `test_team_manage.py`) with live captures `suggestions_list_consultant_redacted.json` and `meeting_attendees_mentor_redacted.json`. Guides: `fe-integration-guide-business-builder-suggestions.md`, `fe-integration-guide-calendar-v2.md`.
**Roll back:** revert the PR. **Follow-up:** FE should not render or `mailto:` these fields for other roles (null-safe).
