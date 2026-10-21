# SOP — Notification read paths filter by the member's current role

| | |
|---|---|
| **Shipped** | Branch `fix/notifications-read-filter` → PR to `develop` (link in the PR) |
| **Date** | 2026-10-21 |
| **Area** | Notifications (Module 20): `app/services/notifications/{audience,service,registry}.py`, `app/api/v1/endpoints/notifications.py` |
| **Closes** | Dashboard re-walk gap 12 and Assessment gap A5 (FE repo checklists) |

## What shipped

Rows stored **before** the fan-out audience fixes (#150 validation, #152 assessment, #153
dashboard/mission/marketing) were still in mentors' and consultants' inboxes. Every notification
**read** path now hides a row from any role the type is no longer delivered to. It is a read
filter only: nothing is deleted or rewritten.

## Why

Fan-out fixes only change what is written from now on. A legacy `mission.ready`,
`dashboard.briefing.ready` or `assessment.completed` row kept showing (and counting as unread) for
a mentor or business consultant, which leaks mission and briefing copy to a role that cannot open
those pages.

## How

* **One audience map.** `audience.py::AUDIENCE` (type → allowed roles) is the single source.
  `registry.py` builds each restricted type's recipients from it (`_audience(event)`), and the read
  paths build their SQL predicate from it (`audience_filter(role)`). They cannot drift; a test
  (`test_every_audience_rule_matches_actual_fan_out`) asserts fan-out recipients equal the map for
  every ruled type.
* **SQL, not Python.** `audience_filter(role)` is `type NOT IN (<types hidden from role>)` (plus a
  JSONB check for `kpi.threshold.crossed`), applied inside the query, so `LIMIT`, cursors and counts
  only ever see visible rows (same approach as `activity_visibility_filter`).
* **Current role.** `membership.role` is read per request, so a role change re-filters on the next
  call. The stream computes its initial `unread` with the role it already looks up.
* **Payload-dependent type.** `kpi.threshold.crossed` goes to `FINANCE_ROLES` when
  `data.audience == "finance"`, otherwise `MARKETING_ROLES`.
* **Rejected:** deleting or soft-hiding legacy rows (irreversible, and wrong again after a role
  change); filtering in Python after the query (breaks pagination and counts).

## Audience map

| Types | Visible to |
|---|---|
| `mission.ready`, `mission.completed`, `mission.streak.milestone`, `dashboard.briefing.ready` | founder, team_member |
| `assessment.completed`, `validation.smoke_test.milestone`, `validation.insight.detected` | founder, team_member |
| `assessment.quarterly.due`, `business.suggestion.created` | founder |
| `finance.runway.low` | founder, team_member, accountant |
| `marketing.post.published`, `marketing.campaign.{launched,completed,readout.ready}` | founder, team_member |
| `kpi.threshold.crossed` | by payload: `audience=finance` → founder, team_member, accountant; else founder, team_member |
| **No rule (open, visible to every role):** `document.*`, `business.suggestion.{approved,rejected}`, `business.artifact.completed`, `business.plan.generated`, `roadmap.*`, `healthscore.*`, `workspace.member.joined`, `team.comment.mentioned`, and any unknown type | all roles |

An unruled type was delivered under the open default, so it stays visible. Legacy
`assessment.quarterly.due` rows follow the current founder-only fan-out rule.

## What's involved

| Path | Change |
|---|---|
| `app/services/notifications/audience.py` | new: map, `audience_filter`, `audience_visible`, `audience_roles` |
| `app/services/notifications/registry.py` | specs for ruled types use `_audience(event)`; old per-audience helpers removed |
| `app/services/notifications/service.py` | `list_notifications`, `unread_count`, `mark_read`, `mark_all_read` take `role` |
| `app/api/v1/endpoints/notifications.py` | pass `membership.role`; stream `_unread(…, role)` |

No migration, no new config, no response-shape change. A hidden row requested by id returns the
normal `404 NOT_FOUND`.

## Verification

* Unit: `tests/services/notifications/test_read_filter.py` (12), `tests/api/notifications/test_read_filter.py` (3),
  stream role test in `tests/api/notifications/test_stream.py`. Covers: a seeded legacy `mission.ready`
  hidden from a mentor and counted out of unread; founder still sees it; role change re-filters;
  pagination only counts visible rows; mark-read 404 and read-all leave the hidden row untouched;
  unruled types stay visible; fan-out ≡ map.
* Live: `e2e/test_notifications_read_filter.py`, captures in `e2e/_captures/notifications_read_filter/`.
  The stream's initial `unread` frame is unit-tested only (not captured live).

## Operate / roll back

Pure code. Revert the PR to restore the old behaviour; no data to restore. To change who sees a
type, edit `AUDIENCE` (both fan-out and reads follow).

## Follow-ups

* Role changes have no endpoint yet; the e2e flips the role in the DB.
* If new restricted notification types are added, add them to `AUDIENCE` (an unruled type is open).
