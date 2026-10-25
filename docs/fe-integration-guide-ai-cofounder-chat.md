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

---

## 5. Failed and stale replies (fix D9, `fix/finance-d9-d10`)

A reply no longer stays `generating` forever. The assistant turn ends `failed` when (a) the model returns an empty completion (ends right away, seconds, no retries), or the worker's last
retry (5th attempt, about 7.5 minutes of backoff) still errors for another reason, or (b) it is still `generating` more than `AI_REPLY_STALE_SECONDS` (900 s) after it was asked (worker down,
job lost). `content` then holds a founder-visible sentence, so the FE can show it as the bubble text:
`"Sorry, I couldn't put a reply together. Please try asking again."` (over the daily AI budget it is
`"Today's AI usage limit for your workspace has been reached. Try again tomorrow."`). There is no new
endpoint or field: keep polling `GET /ai/conversations/{id}` and treat `status: "failed"` as terminal.

Captured live (the model call forced to fail with an empty completion; `e2e/_captures/finance_d9_d10/`).
Right after sending, `chat_started.json`: the assistant turn is `generating` with `"content": ""`. After the
worker's last attempt, `chat_after_last_retry.json`:

```json
{
  "data": {
    "id": "a96d006f-7241-4b60-8346-a216078ce501",
    "title": "What levers should I look at first?",
    "created_at": "2026-10-24T22:30:54.038880+00:00",
    "updated_at": "2026-10-24T22:30:54.067566+00:00",
    "messages": [
      {
        "id": "3f1363be-d87b-4ff0-9b55-dc4305d6097c",
        "role": "user",
        "status": "ready",
        "content": "What levers should I look at first?",
        "created_at": "2026-10-24T22:30:54.066566+00:00"
      },
      {
        "id": "0c60df40-357d-4c87-863e-09b4f6864633",
        "role": "assistant",
        "status": "failed",
        "content": "Sorry, I couldn't put a reply together. Please try asking again.",
        "created_at": "2026-10-24T22:30:54.067566+00:00"
      }
    ]
  },
  "meta": null
}
```

**What the FE must do.** Render `failed` as an error bubble using `content` and offer "Try again" (a new
`POST .../messages` is allowed straight away; the 409 `REPLY_IN_PROGRESS` guard no longer blocks on a dead
turn, because the stale sweep runs first). Keep the bounded polling from section 2, but expect the
failure to arrive after several minutes when the model keeps erroring, so stop at your own timeout and
show the same wording rather than "Thinking..." forever. The stale sweep also runs on `GET`, so reopening
an old thread repairs it.

`chat_stale_failed.json` shows a turn whose job never ran, read back after it aged past the window: it is
`failed` with the same sentence. (In that capture the aged turn sorts first only because the test rewrote
its `created_at` two hours into the past; a real stale turn keeps its place.)

| Behaviour | Verified live | Source |
|---|---|---|
| Last retry fails, turn ends `failed` with the sentence | yes | `chat_after_last_retry.json` (LLM stubbed to fail; real worker loop, real server) |
| Stale `generating` turn failed on `GET`, next send allowed | yes | `chat_stale_failed.json`, follow-up `202` asserted in `e2e/test_finance_d9_d10.py` |
| Real model no longer returns empty for "What levers should I look at first?" | **no, not exercised** | cause inferred from the code (reasoning tokens exhausting the cap); the lead verifies on staging |
| Over-budget wording | unit only | `tests/worker/test_cofounder_reply_handler.py` |
