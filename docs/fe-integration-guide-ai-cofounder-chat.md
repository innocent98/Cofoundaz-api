# FE Integration Guide — AI Co-Founder Chat (Module 03, v1)

Bodies below are from **live captures** against a running local server (API + worker +
Postgres/Redis, `LLM_PROVIDER=openai`) on 2026-10-17, driving the real endpoints end to end
with a real founder session. IDs/text are from that ephemeral run (shapes are exact).

> **This is the interactive chat that previously did not exist.** The AI Co-Founder index
> (`fe-integration-guide-ai-cofounder.md` §3) said "interactive chat is NOT built — the `/ai`
> router exposes only `GET /ai/status`." As of this change there is a **two-way conversation
> backend**. Update that index's §3 when this lands.

Base path `/api/v1/ai`. Every route needs a Bearer token **and** `X-Workspace-Id`. Access is
**founder or team_member only** (mentors/accountants/legal advisors get `403`). Conversations are
**private to the member who created them** — another member in the same workspace gets `404` for a
chat they didn't start, and `GET /ai/conversations` returns only the caller's own. Success
envelope `{"data": …, "meta": null}`.

---

## 0. Shape: Pattern B (202 → poll), reusing the app's async seam

Sending a message does **not** return the answer inline. It stores the founder's turn + an empty
**`generating`** assistant turn, enqueues `ai.cofounder.reply`, and the worker fills the assistant
turn from the LLM (with workspace context). The FE **polls the conversation** until the last
assistant turn is `ready` (or `failed`) — same bounded-poll + honest-timeout pattern as the
business-plan / canvas AI-fill. No streaming in v1.

A message object:
```json
{ "id": "…", "role": "user" | "assistant", "status": "ready" | "generating" | "failed",
  "content": "…", "created_at": "2026-10-17T…Z" }
```
`role: "user"` turns are always `ready`. An `assistant` turn is `generating` → `ready` (content
filled) or `failed` (LLM error / over the daily AI budget — surface "AI unavailable right now",
there is no partial content).

---

## 1. Endpoints (live captures)

### 1a. `POST /ai/conversations` — start a conversation
Body `{"message"?: string}`. With a `message`, it starts the first turn (same as 1c); without,
it creates an empty conversation. **201.**
```json
{ "data": {
    "id": "70cf9b01-1cbe-4d04-9289-23eef2c8e94e",
    "title": "What are the two most important things I should focus on this month?",
    "created_at": "2026-10-17T…Z", "updated_at": "2026-10-17T…Z",
    "messages": [
      { "id": "…", "role": "user", "status": "ready",
        "content": "What are the two most important things I should focus on this month?",
        "created_at": "2026-10-17T…Z" },
      { "id": "…", "role": "assistant", "status": "generating", "content": "",
        "created_at": "2026-10-17T…Z" }
    ] } , "meta": null }
```
`title` is derived from the first message (≤80 chars); `null` for an empty conversation.
Messages are ordered oldest-first, user before assistant within a turn.

### 1b. `GET /ai/conversations` — list (newest activity first)
`{"data": [ {"id","title","created_at","updated_at"} … ]}` (no messages).

### 1c. `POST /ai/conversations/{id}/messages` — send a message
Body `{"content": string}` (1–8000 chars; empty → `422`). **202**, returns the **assistant**
turn to poll:
```json
{ "data": { "id": "…", "role": "assistant", "status": "generating", "content": "",
            "created_at": "2026-10-17T…Z" }, "meta": null }
```

### 1d. `GET /ai/conversations/{id}` — the thread (poll this)
`{"data": {"id","title","created_at","updated_at","messages":[…]}}`. Poll a few seconds after
sending until the last assistant turn's `status` ≠ `generating`. Live capture after the worker
drained (real `gpt-5.6-luna`, answering for a gig-worker fintech startup):
```json
{ "role": "assistant", "status": "ready",
  "content": "## 1. Ship a narrow, reliable MVP in one market\n\nDo **not** build a full West Africa product yet. Pick:\n- One country\n- One primary gig-worker segment\n- One savings behavior\n- One payment flow\n- One licensed payment/banking partner\n…" }
```
Content is freeform text (the model may use light markdown). Render opaquely; do not parse.

---

## 2. FE guidance
- After `POST …/messages` (202), optimistically show the founder's turn + a "thinking…" assistant
  bubble, then poll `GET /ai/conversations/{id}` (e.g. every ~1.5s, bounded to ~30s). On `ready`,
  render content; on `failed` or timeout, show an honest "couldn't reach your co-founder — try
  again." Do not poll forever.
- Wire the dock drawer, the `/ai` page, and the dashboard "Tell me more" (which opens the drawer)
  to this. Replace the honest "coming soon" copy.
- `GET /ai/status` (`over_budget`) still governs availability — if over budget, the assistant turn
  comes back `failed`.

## 3. Errors
| Code / status | When |
|---|---|
| `422` | empty/too-long `content`; malformed body |
| `404` NOT_FOUND | unknown conversation id, one in another workspace, or one created by a different member |
| `409` REPLY_IN_PROGRESS | a new message was posted while this conversation's previous assistant turn is still `generating` — wait for it (keep polling), then allow the next send |
| `401` / `403` | unauthenticated / missing `X-Workspace-Id` / role other than founder or team_member |

## 4. Verification table
| Behaviour | Live? | Source |
|---|---|---|
| `POST /ai/conversations` with message → 201, user `ready` + assistant `generating` | ✅ | create capture (§1a) |
| Poll `GET /ai/conversations/{id}` → assistant `generating` → `ready` with real LLM answer using workspace context | ✅ | §1d capture (real `gpt-5.6-luna`) |
| `POST …/messages` → 202 assistant turn | ✅ | live run |
| Message order stable (user before assistant) | ✅ | explicit created_at gap; `tests/api/test_ai_chat.py` |
| Empty content → 422; cross-tenant → 404; unauth → 401/403 | ✅ | `tests/api/test_ai_chat.py` |
| Over budget → assistant `failed` | ✅ unit | `tests/worker/test_cofounder_reply_handler.py` |
| Deferred (NOT in v1): token streaming, specialist-routing badges, action chips, suggestions, memory | — | scoped out; see SOP |
