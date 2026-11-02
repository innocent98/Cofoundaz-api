# SOP — Global ⌘K search (`GET /search`)

**What shipped (one line):** A real global search across every module that has data, role-gated per source,
replacing the dashboard's hardcoded 8-item search overlay. Branch `feat/global-search` (stacked on #129 for
the shared role sets) → PR.

## Why
Dashboard gap #8 (`cofoundaz/docs/checklist/dashboard-prd-gaps.md`): the ⌘K box (PRD §2.1) only filtered a
hardcoded list and never navigated. Searching "problem" found nothing, though a milestone matched.

## How (key decisions)
| Decision | Why | Rejected |
|---|---|---|
| One declarative `SOURCES` table (type, group, roles, href, query) + a `PAGES` table | Adding a module = one row. Gates are visible in one place | Per-module search endpoints |
| Each source gated by its module's roles (`FINANCE_ROLES`, `MARKETING_ROLES`, founder/TM for validation/calendar/AI, everyone for roadmap/docs/business/mission). Pages filtered the same way | Search must never reveal what the module itself would 403 | One global "can search" check |
| AI conversations filtered to `created_by == caller` | Chats are private per creator | Workspace-wide |
| ILIKE substring with `%`/`_`/`\` escaped, min 2 / max 100 characters, 5 per source, 60/min | Simple, predictable, no index migration at current data sizes | Postgres full-text (tsvector + migration): revisit when data grows |
| Journal excluded | No title, and content is encrypted at rest. Server-side decryption to search would undercut that design | Decrypt-and-scan |
| Static hrefs to list pages; deep links only for documents (`/documents/{id}`) and chats (`/ai?c={id}`) | Only those FE routes accept an item ID today | Inventing query params the FE ignores |

## What's involved
`app/services/search/{__init__,service}.py` (new), `app/api/v1/endpoints/search.py` (new), `app/api/v1/api.py`
(mount `/search`), `tests/services/test_search.py` (new, 11 tests), guide `docs/fe-integration-guide-search.md`,
live captures `e2e/_captures/search_live/`. No migration.

## Verification
- Search service 100% covered. Full suite / gates: see the PR.
- Live on local docker: founder finds milestone + tasks; founder vs mentor page filtering; short query; 401.

## Operate / roll back
No migration, no config. Revert the PR.

## Follow-ups
- Deep-link query params on list pages (e.g. `/roadmap/milestones?id=`), so a result opens the exact item.
- Contacts once Sales ships. Full-text ranking if data volume grows.
