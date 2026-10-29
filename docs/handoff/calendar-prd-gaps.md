# Calendar: PRD + UI gap list (2026-10-27)

Source of truth: `Cofoundaz_Technical_PRD.md` Module 19 (§19.1 Calendar View to §19.5 Sync Settings, API, entities, events,
notifications) and the access matrix (line ~902: calendar = F ✓, TM ✓, every other role "invited mtgs").
Redirect URLs and provider order: `HANDOFF-master.md` §0b items 18 and 20 (Google now, Microsoft later).

**How this was walked, and what was not.** The three pages (`/calendar`, `/calendar/milestones`, `/calendar/sync`) were read
control by control in source (`app/(dashboard)/calendar/**`, `hooks/useCalendarApi.ts`) and compared with the raw API
(`app/api/v1/endpoints/calendar.py`, schemas, service). **The live browser walk at 1440 + 375 did not happen**: the machine was
at load average 100+ (other sessions' builds), the in-app browser refused `localhost:3055` (:3000 is held by the haven
project's dev server) and my dev server kept dying. The FE is a pure static mock (nothing below depends on pixels), so the
list stands, but the FE session must do the 1440/375/every-role pass before ✅, and add rows for anything visual it finds.

## Headline

The Calendar API is a thin CRUD (`/calendar/events` x5 + `/calendar/milestones`), and **the FE calls none of it**: every number,
title and date on all three pages comes from `CALENDAR_EVENTS_DATA` and friends in `hooks/useCalendarApi.ts`, the header is
hard-coded "July 2026", "today" is hard-coded to the 27th, and every save/click only logs a toast to the console
(`useToast` is a stub that does `console.log`). The Sync page shows a fake "Connected as amara@kolo.africa".

## Product calls (D-CAL)

**Decided 2026-10-27 (Adebayo, via the lead): "Accept D-CAL-1, 2 and 4 defaults." D-CAL-3 is still open (his call, answered separately).** The BE is built to the defaults below, for both Google Testing and verified mode.

Each has a proposed default; I build the default unless told otherwise. Only D-CAL-1 and D-CAL-2 change what gets built.

| ID | Question | Proposed default |
|---|---|---|
| **D-CAL-1** | The PRD's "AI Meeting Assistant" **joins the call**, writes the summary and extracts action items. Joining a Meet/Zoom/Teams call needs a third-party meeting-bot vendor (e.g. Recall.ai, paid, per-minute) plus transcript storage and consent notices. Build that now, or something smaller? | **Smaller:** no bot. After a meeting, the user **pastes notes or a transcript** (or the transcript text field) and the AI writes the summary, decisions and action items; **Add all to tasks** works as in the PRD. The "Invite my AI Meeting Assistant" toggle becomes "Summarise this meeting afterwards" until a bot vendor is chosen. |
| **D-CAL-2** | Google sync: **which Cofoundaz items are pushed** to the user's Google calendar under "Two-way" / "Push only"? | Only items **created in the Calendar module** (`calendar_events`: meetings, tasks, milestones and deadlines you add by hand). Roadmap milestones, marketing posts and compliance dates are **not** pushed (they already live in Cofoundaz and would duplicate). Pulled Google events are **private to the connecting user**; teammates see only a **"Busy" block** (PRD privacy note). Connection is **per user**, not per workspace. |
| **D-CAL-3** | Google's `calendar.events` scope is **sensitive**. While the OAuth consent screen is in "Testing", only listed test users can connect and **refresh tokens expire after 7 days** (sync silently breaks weekly). Going public needs Google verification (privacy-policy URL, domain verification, a demo video). Will you submit for verification before launch? | Yes, **Adebayo's task** (goes in the "waiting on you" list). Until then the Sync page shows the PRD's "needs attention, reconnect" banner when a refresh fails, and we add test users in the Google console. |
| **D-CAL-4** | A meeting with **external attendees**: Cofoundaz emails "{Founder} invited you: {title} · {date}" with an `.ics` (PRD). If the organiser also has Google sync on, Google would email the same people. | Cofoundaz sends the invite + ICS from its own mail path; the Google push uses `sendUpdates=none` so nobody gets two emails. |

Lead-defaultable (not asked unless you disagree; all reversible):

| ID | Default |
|---|---|
| D-CAL-5 | **Timezone:** add a per-event IANA `timezone` (default `Africa/Lagos`, the only market today). Today's naive wall-clock stays valid: existing rows become `Africa/Lagos`. Google needs a zone to round-trip. |
| D-CAL-6 | **Invited-only access** for mentor, accountant, legal advisor, consultant, investor: read-only, only meetings whose attendee email matches their user email (or `user_id` for internal attendees). No create/edit. Matches the PRD matrix. |
| D-CAL-7 | **Layers** in the feed: `milestone` (roadmap), `compliance` (empty until Legal ships, adapter ready), `meeting`, `team` (calendar events of type task/deadline/milestone created here), `marketing` (scheduled content-calendar entries). |
| D-CAL-8 | **Reminder defaults** per PRD (milestones 7d + 1d, compliance 14d + 7d + 1d, meetings 15 min); stored per workspace, editable by founders. The existing roadmap `due_soon`/`overdue` scheduler jobs keep running and **dedupe** with the milestone rule. |
| D-CAL-9 | **Outlook** card is shown as "Coming soon" with the same paths reserved (`/sync/outlook/...`). **Apple Calendar** card (not in the PRD, mock only) is removed. The three extra toggles on the Sync page (block focus time, remind before deadlines, show weekends) are not in the PRD and not backed: remove (reminders move to the Reminders page). |

## Gaps

Legend: ✅ meets PRD · ❌ gap · Layer **BE** / **FE** · Severity **Sec** > **High** > **Med** > **Low**

### A. Backend

| # | Area | Gap | PRD | Sev | Proposed fix |
|---|---|---|---|---|---|
| B1 | Feed | No `GET /calendar/feed?from&to&layers`. `/events` returns only the Calendar table; milestones come from a second route; marketing schedule never appears. FE would need 3 calls and client merge. | §19.1, API | High | `GET /calendar/feed`: one list of `{id, layer, source_ref, title, starts_at/date, ends_at, all_day, status, drawer_ref}` merged from events, roadmap milestones, content-calendar entries, meetings, (compliance later), plus `busy` blocks for teammates' synced events. `layers=` filter, 366-day cap, tenant-scoped. |
| B2 | Meetings | No meetings at all: attendees (internal + external emails), agenda, location/video link, timezone, summary, decisions, action items, transcript. | §19.3, entities `meetings` | High | `calendar_meetings` (1:1 with an event of type meeting) + `/calendar/meetings` CRUD; attendees as rows (`calendar_attendees`: email, user_id?, response). |
| B3 | Invites | No invite email, no ICS. | §19.3 | High | On create/update with external attendees: queue email "{Founder} invited you: {title} · {date}" with a generated `.ics` (REQUEST/CANCEL, stable UID, SEQUENCE). Per-attendee send state. |
| B4 | Assistant | `POST /calendar/meetings/{id}/assistant` missing; no summary/actions. | §19.3, event `meeting.summary.ready` | High | Per D-CAL-1: job that turns pasted notes/transcript into summary + decisions + action items (strict-schema walker test, real-model run in the FE walk). `POST .../actions/add-to-tasks` creates roadmap tasks. Notification "Meeting summary ready: {title} - {n} action items". |
| B5 | Reminders | No reminder rules (`/calendar/reminder-rules`); only hard-coded roadmap due-soon jobs. No "Meeting starting in 15 minutes" notification. | §19.4 | High | `calendar_reminder_rules(startup_id, event_kind, offsets_minutes[])` seeded with PRD defaults; scheduler job fires once per (event, offset) via `ScheduledRun` claim; notifications per PRD copy. |
| B6 | Sync | No sync at all (no tables, no OAuth, no worker). | §19.5, `POST /calendar/sync/{provider}/connect` | High | Per-user Google connection: `GET /calendar/sync` (status), `POST /calendar/sync/google/connect` (returns consent URL with signed `state`), `GET /calendar/sync/google/callback` (public; verifies state; exchanges code), `PATCH /calendar/sync/google` (direction, calendar id), `GET .../calendars` (picker), `POST .../sync` (Sync now), `DELETE .../google` (revoke at Google + delete tokens; keep imported rows? see below). Fernet-encrypted tokens (same pattern as Finance), refresh on expiry, `needs_attention` on `invalid_grant`. Worker job syncs every 15 min with incremental `syncToken`. |
| B7 | Sync data | No place for imported events or the Cofoundaz-to-Google id map. | §19.5 privacy note | High | `calendar_external_events(connection_id, google_id, etag, title, starts/ends, all_day, busy_only)`; `calendar_events.external_id/etag` for pushed rows. Imported details visible to the owner only; teammates get busy blocks (title omitted server-side, not hidden by the FE). |
| B8 | Access | Routes are founder + team only; PRD gives every other role **read of meetings they are invited to**. Accountant gets 403 on everything, including an invite. | matrix | Med | Invited-only read route set per D-CAL-6 (uniform 404 for non-invited). |
| B9 | Events | `type` has four values but no `visibility`, `location`, `video_url`, `timezone`, `source_ref`. PRD entity has `visibility`, `source_ref`. | entities | Med | Add `timezone`, `location`, `video_url`, `visibility` (`team`/`private`), `source_ref`. Naive times stay valid (backfill `Africa/Lagos`). |
| B10 | Events | Date-only model: `event_date` + `start_time`; events cannot span days; no `ends_at` date. | §19.1 | Med | Add `end_date` (nullable, >= `event_date`); feed emits `starts_at/ends_at`. |
| B11 | Events | `GET /events` has no `q`, no pagination, no cap on range; `PATCH` does not report a no-op; delete has no audit row. | | Low | Cap 366 days; audit `calendar.event.*`. |
| B12 | Milestones | `/milestones` returns no `days_left`; FE shows a "days left" chip per PRD and "N of M complete this quarter" (hard-coded "3 of 7"). `tasksInfo` ("11 of 13 tasks") not backed. | §19.2 | Med | Add `days_left`, and `summary {total, complete, quarter_complete}`; add `tasks_done/tasks_total` from roadmap tasks (the data exists). |
| B13 | Notifications | `milestone deadline tomorrow`, `meeting starting in 15 minutes`, `meeting summary ready` do not exist in the registry. | notifications | Med | Register three events with PRD copy; deep links to `/calendar?event=`. |
| B14 | Search/Dashboard | Dashboard "Upcoming (next 7 days)" and global search read their own sources; events created in Calendar do not appear. | §03, search | Low | Point both at the feed service. |

### B. Frontend (control by control, from source)

| # | Control | Observed | PRD | Sev | Proposed fix |
|---|---|---|---|---|---|
| F1 | `/calendar` data | All events are `CALENDAR_EVENTS_DATA` mock; month header literally "July 2026"; "today" highlight hard-coded to 27; week header "July 12 - 18, 2026". | §19.1 | High | Drive from `/calendar/feed` for the visible range. |
| F2 | Prev / next (month, week) | Only `console.log` toast; no navigation. Add a **Today** button. | §19.1 | High | Real range navigation; URL `?view=&date=`. |
| F3 | Month / Week / Agenda tabs | Switch local state only; Week is hour rows with mock `dayIndex`; Agenda is a mock list. | §19.1 | High | Same feed, three layouts; Agenda grouped by day with a "Nothing scheduled" empty state. |
| F4 | Type filter chips (All / Milestone / Meeting / Deadline / Task) | Filter the mock array client-side; PRD asks for **toggleable source layers in a legend**, each with ramp tone + icon (never hue alone). | §19.1 | Med | Legend toggles for the five layers with icons; persist choice per user. |
| F5 | `+ Event` button + modal | Fields title, type, date only; **Save logs "New event created" and does nothing** (no POST). No time, all-day, end, description, attendees. Button label is "+ Event"; PRD "+ New" (event / meeting). | §19.1/19.3 | High | Real create drawer: event vs meeting; times, all-day, tz, location/video, description, attendees, agenda; 422 field errors shown inline. |
| F6 | Event click (month cell, week block, agenda card) | Toast "Opened event: ..." only. No drawer, no edit, no delete. | "click → type-appropriate drawer" | High | Drawer per layer: event (edit/delete), meeting (attendees, agenda, summary, actions), milestone (opens in Roadmap), marketing post (opens in Marketing). |
| F7 | Month cell overflow | `overflow-hidden` with no "+N more"; days with 3+ events clip. | | Med | "+N more" popover. |
| F8 | `/calendar/milestones` | Static list; "3 of 7 complete this quarter" hard-coded; cards toast; `+ Milestone` modal saves nothing and (per the API) would create an **event**, which then never shows in this list. | §19.2 | High | Read `/calendar/milestones`; "days left" chip; the button creates a roadmap milestone deep link (or is removed), not a calendar event. |
| F9 | `/calendar/sync` Google card | Shows fake "Connected as amara@kolo.africa"; Connect/Disconnect flip local state. | §19.5 | High | Real connect (redirect to consent URL), "Connected as {email}", last sync, **Sync now**, **Disconnect** with confirm ("Disconnect Google? Imported events are removed; your Cofoundaz events stay."), "needs attention" banner. |
| F10 | Direction radio, calendar picker, privacy note | Absent. | §19.5 | High | Radio with the three PRD labels; picker from `.../calendars`; privacy note verbatim. |
| F11 | Outlook / Apple cards | Local toggles; Apple is not in the PRD. | D-CAL-9 | Med | Outlook "Coming soon", Apple removed. |
| F12 | Three Sync toggles (focus time, deadline reminders, weekends) | Local state, not in PRD, not backed. | D-CAL-9 | Med | Remove; reminders live on the Reminders page. |
| F13 | `/calendar/meetings` | **Page does not exist.** | §19.3 | High | List (upcoming / past), create, drawer, post-meeting view (summary, decisions, action items with Add all / per-item, transcript accordion). |
| F14 | `/calendar/reminders` | **Page does not exist.** | §19.4 | High | Rules per event kind with editable offsets (founder edits; TM read-only). |
| F15 | Sub-navigation | Tabs reach Month/Week/Agenda only; Milestones and Sync reached by URL or sidebar; no Meetings / Reminders entries. | §19 routes | Med | One sub-nav: Calendar, Milestones, Meetings, Reminders, Sync. |
| F16 | Roles | Page renders for everyone the sidebar shows it to; PRD: accountant sees Calendar; others see only invited meetings. | matrix | Med | Invited-only view (list of their meetings, read-only drawer) for non-F/TM roles. |
| F17 | Toast | `useToast` is a console.log stub on all three pages. | | Med | Use the app's real toast. |
| F18 | 375px | Not walked (see top). | | ? | FE session walks 375 on all five pages; Week grid (8 columns) needs a horizontal-scroll or day-by-day layout. |

## Deliberately not in this slice

- Meeting-bot that joins the call (D-CAL-1).
- Outlook / Microsoft sync (per decision 20); the callback path `/calendar/sync/outlook/callback` stays reserved.
- Compliance deadlines layer data (needs Legal, Victoria's PR); the feed accepts the layer and returns none.
- A real Google round trip: the code is tested against a fake Google client; the first real connect is for Adebayo with a Google test user (see the FE guide's verification table).


## Ledger / handoff (2026-10-27)

- **Branch** `feature/calendar-be` in worktree `/Users/adebayovictor/Documents/dev/cofoundaz/wt/calendar-be` (API repo, migration `0079_calendar_meetings_sync`, chains after 0076). Gaps B1 to B13 built; B14 (dashboard/search read the feed) not.
- **Done:** feed, event fields, meetings + invites/ICS, assistant (paste notes), reminder rules + scanner + notifications, Google sync (per-user OAuth with signed state + browser-proof cookie, Fernet tokens, refresh, `needs_attention` reconnect state, pull/push, picker, disconnect/revoke, worker job). Opus review done, 6 of 7 findings fixed (the 7th was already handled). SOP `docs/sop/2026-10-27-calendar-be-meetings-sync.md`; checklist updated.
- **Left:** (1) confirm the full-suite + e2e runs were green (`/tmp/cal-full.txt`, `/tmp/cal-e2e-out.txt` if still there), (2) generate `docs/fe-integration-guide-calendar-v2.md` from `e2e/_captures/calendar_v2/` with the template at the scratchpad `guide_template.md` (placeholders `@@capture_name@@`), (3) open the API PR (no attribution; head `feature/calendar-be`), (4) FE session.
- **FE blockers to tell the FE session:** connect must call with `credentials: "include"` (HttpOnly `cfz_cal_oauth` cookie), callback lands on `{APP_BASE_URL}/calendar/sync?connected=google|error=<code>`.
- **Ops for Adebayo:** set `GOOGLE_CLIENT_ID/SECRET`, `FINANCE_CONNECTIONS_ENCRYPTION_KEY`, `API_PUBLIC_URL` per environment; restart the worker; register the three redirect URIs; add test users while the app is in Testing; D-CAL-3 (verification) open.
- **Live walk** of the three calendar pages did not happen (machine load, browser permission); the FE session must do the 1440/375 every-role walk.
