# FE Integration Guide — Global search (⌘K) `GET /api/v1/search`

**Provenance.** Live captures from the local docker stack, 2026-10-17, as the local test founder and as a
mentor who joined through the new invite flow: `e2e/_captures/search_live/*.json` (verbatim). Unit
tests: `tests/services/test_search.py` (100% of the service).

## 1. What it is, in FE terms
The top bar's search box (PRD §2.1, placeholder **"Search anything — docs, tasks, contacts, conversations…"**,
shortcut `⌘K`). Today the overlay shows a hardcoded 8-item list that never navigates. Wire it to this endpoint
and navigate to each result's `href`. Scope (decision D-DASH-3): app pages, roadmap milestones and tasks, mission
tasks (last 14 days), documents, the caller's own AI conversations, calendar events, validation
assumptions/experiments/surveys, finance transactions and invoices, marketing campaigns/posts/keywords/segments.
**Not searchable:** journal entries (they have no title and are encrypted at rest) and Sales contacts (no backend
yet). Drop the word "contacts" from the placeholder until Sales ships.

## 2. Request
`GET /api/v1/search?q=<text>`. Bearer + `X-Workspace-Id`, any active member. `q` is trimmed and inner whitespace
collapsed; **fewer than 2 characters returns no results** (not an error); over 200 characters → 422.
Rate limit: 60/minute per user, so **debounce ~250 ms** and cancel stale requests.

## 3. Response (live, founder, `founder_problem.json`)
```json
{
    "data": {
        "query": "problem",
        "results": [
            {
                "type": "milestone",
                "group": "Roadmap",
                "id": "9797de96-3ac8-47b1-918d-15f7fe9fd2b9",
                "title": "Write your problem statement",
                "subtitle": "Milestone · due 2026-10-24",
                "href": "/roadmap/milestones"
            },
            {
                "type": "roadmap_task",
                "group": "Roadmap",
                "id": "9dc4a38f-2ec7-46c5-82d5-221b3363b71f",
                "title": "Describe the problem in one paragraph",
                "subtitle": "Task in Write your problem statement",
                "href": "/roadmap"
            },
            {
                "type": "roadmap_task",
                "group": "Roadmap",
                "id": "b7257c42-f7ac-4f50-be98-a5f6ac97b726",
                "title": "List who has this problem",
                "subtitle": "Task in Write your problem statement",
                "href": "/roadmap"
            }
        ]
    },
    "meta": null
}
```
(`·` is the JSON escape for "·".) Pages come first (`"type": "page"`, `"group": "Pages"`, `"subtitle": null`,
`id` = the route), then the other sources in a fixed order. Each source returns **at most 5**. Render the results
**grouped by `group`** in the order they arrive, with `title`, then `subtitle` when it isn't null, and route
with `router.push(href)`.

`type` values: `page`, `milestone`, `roadmap_task`, `mission_task`, `document` (`/documents/{id}`), `conversation`
(`/ai?c={id}`, which opens that chat), `calendar_event`, `assumption`, `experiment`, `survey`, `transaction`, `invoice`,
`campaign`, `post`, `keyword`, `segment`. Most hrefs are the module's list page. Only documents and conversations
deep-link to the exact item.

## 4. Role filtering (it's done server-side; never filter client-side)
Results only include what the caller's role can open in that module (pages too, so they mirror the sidebar
rule "hidden, not disabled"). Live: the mentor's `q=fin` (`mentor_fin.json`):
```json
{
    "data": {
        "query": "fin",
        "results": []
    },
    "meta": null
}
```
while the founder's `q=fin` (`founder_fin.json`) returns the Finance Hub, Runway, Invoices, Expenses and Budgets pages.
AI conversations are private: each person only ever finds their own.

**2026-10-20 (D-MIS-8):** mission tasks and the "Today's Mission" page are now core-role only (founder + team member),
and the "Assessment" page is offered to team members as well as founders. See
`docs/fe-integration-guide-dashboard-role-leaks.md` §2 for the captures.

## 5. Empty and error states
- `q` under 2 characters (`founder_too_short.json`): `{"data": {"query": "a", "results": []}, "meta": null}`. Show
  a hint ("Type at least 2 characters"), not "No results".
- No matches: "No results for '{q}'."
- 401 without auth (`no_auth_401.json`); 422 for `q` > 200 characters (unit-tested).
- `%` and `_` are matched literally (searching `100%_` finds the text "100%_").

## 6. Verification
| Behaviour | Live? | Source |
|---|---|---|
| Founder finds roadmap milestone + tasks | ✅ | `founder_problem.json` |
| Pages, role-filtered (founder vs mentor) | ✅ | `founder_fin.json`, `mentor_fin.json` |
| Too-short query | ✅ | `founder_too_short.json` |
| 401 | ✅ | `no_auth_401.json` |
| Every source + per-role gates, tenant isolation, private conversations, literal `%`/`_`, 5-per-source cap, 422 | ❌ unit tests | `tests/services/test_search.py` |
