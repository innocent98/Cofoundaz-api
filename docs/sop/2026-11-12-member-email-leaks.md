# SOP: member e-mail leaks (business suggestions, calendar attendees)

**What shipped:** two more places that gave member e-mail addresses to roles other than founder and team member now return `null` (Adebayo, 2026-11-12: "Founder + TM only"). Follows API #240 (`GET /team/members`). Branch `fix/member-email-leaks`. No migration.

| Endpoint | Before | Now |
|---|---|---|
| `GET/POST /business-builder/suggestions`, approve, reject (`serialize_suggestion`) | `author.email` for every role | `author.email` only for founder and team member (`include_email`); others get the name |
| `GET /calendar/meetings`, `/{id}` (`serialize_meeting`) | `attendees[].email` for every invited role | `null` unless founder or team member; internal attendee `name` falls back to the profile name |

| `person()` / `display_name()` in `app/services/business/service.py` (`updated_by` on canvases and records, `edited_by` on versions, suggestion notification title and `author_name`) | fell back to the member's e-mail when no profile name, readable by all 7 roles | falls back to the neutral label **"A team member"** (never the address) |
| `AttendeeOut.email` (`app/schemas/calendar.py`) | `str` | `str \| None` |

Review follow-up (Opus, lead 26): the investor case in the suggestions test no longer skips (`GET /suggestions` is open to every role); create (consultant: `null`) and approve/reject (founder, team member: present) are asserted; a per-role test covers a member with no name. Grepped for other name-or-email fallbacks: sales analytics and staff/admin screens are founder/TM or staff only; `calendar/invites.py` (inviter name in the mail the founder sends) is left as is. **Fixed in a follow-up commit:** `documents/common.py::owner_map` (and `display_name`) used the e-mail's local part for a nameless owner, which reaches grantees (`shared_by`), share recipients (`sharer_name`) and version `author_name`; it now uses the shared `NAMELESS_MEMBER` ("A team member", moved to `app/core/names.py`). No other `split("@")` name fallback exists in `app/`. Test: `tests/api/test_documents_grants.py::test_a_nameless_owner_is_never_named_by_their_email` (mentor grantee).

**Tests:** `tests/api/test_business_suggestion_email.py` (per role), `tests/api/test_calendar_meetings.py` (per role + name fallback); e2e journeys updated (`test_business_builder.py`, `test_team_manage.py`) with live captures `suggestions_list_consultant_redacted.json` and `meeting_attendees_mentor_redacted.json`. Guides: `fe-integration-guide-business-builder-suggestions.md`, `fe-integration-guide-calendar-v2.md`.
**Roll back:** revert the PR. **Follow-up:** FE should not render or `mailto:` these fields for other roles (null-safe).
