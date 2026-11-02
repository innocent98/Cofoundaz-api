# FE integration guide: Settings → AI preferences

Bodies below are pasted from responses captured by `e2e/test_ai_settings.py` against a real running server
(`e2e/_captures/ai_settings/`). Base path `/api/v1`. Envelope: success `{data, meta}`, error
`{error:{code,message,field_errors[]}}`. Auth: bearer token + `X-Workspace-Id`.

## What this is

The three PRD 03.5 switches for the AI Co-Founder: **Proactive suggestions**, **Daily briefing**, **Tone**
("Straight shooter" / "Encouraging"). They are stored **per member per workspace** (a teammate's choices never
change yours) and they take effect, they are not just saved. **Not included:** the "What I remember" list, "Forget
this" and "Clear all memory". There is no memory store yet, so the FE hides that whole section.

Access: founders and team members (the people who have the AI Co-Founder). Other roles get **403**.

## Endpoints

### `GET /ai/settings` (200)
Defaults apply until a member saves anything (no row is needed):
```json
{
  "data": {
    "proactive_suggestions": true,
    "daily_briefing": true,
    "tone": "encouraging"
  },
  "meta": null
}
```

### `PATCH /ai/settings` (200)
Send only what changes; the response is always the full object. `tone` is exactly `straight_shooter` or `encouraging`
(a fixed set, never free text).
```json
{
  "data": {
    "proactive_suggestions": true,
    "daily_briefing": true,
    "tone": "straight_shooter"
  },
  "meta": null
}
```
```json
{
  "data": {
    "proactive_suggestions": false,
    "daily_briefing": false,
    "tone": "straight_shooter"
  },
  "meta": null
}
```

Errors (all **422** `VALIDATION_ERROR`): a tone outside the set, an unknown field, an empty body, or a `null` value.
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "tone",
        "message": "Input should be 'straight_shooter' or 'encouraging'"
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "memory",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Send at least one setting, with a value.",
    "field_errors": []
  }
}
```

## What each setting actually changes (build the helper text from this)

| Setting | Effect when OFF / chosen |
|---|---|
| **Daily briefing** off | The dashboard's three briefing blocks come back with `status: "off"` for this member and nothing is generated for them. They also stop getting the "Your daily AI briefing is ready" notification. Teammates who kept it on are unaffected. If nobody opens the dashboard with it on, no briefing is generated that day. |
| **Proactive suggestions** off | On the dashboard briefing, the AI's suggested next steps (`briefing.actions`) come back empty and the Opportunities block is `status: "off"`. Risks and the briefing prose stay. They also stop getting the "New course recommended for your stage" notification. Suggestions a human makes (a consultant's Business Builder suggestions) are **not** affected. |
| **Tone** | A fixed sentence is added to the AI's instructions: for this member's AI Co-Founder chat replies, and for the shared daily briefing (one per workspace) the **earliest founder's** tone is used. It changes new replies and newly generated briefings, not past ones. |

Dashboard `GET /dashboard/summary` with the briefing switched off (captured, with proactive suggestions also off):
```json
{
  "briefing": {
    "status": "off",
    "message": null,
    "id": null,
    "actions": []
  },
  "risks": {
    "status": "off",
    "message": null,
    "items": []
  },
  "opportunities": {
    "status": "off",
    "message": null,
    "items": []
  }
}
```
Briefing switched back on while proactive suggestions stay off. This workspace has no completed assessment, so the briefing
is `empty`; note Opportunities stays `off`:
```json
{
  "briefing": {
    "status": "empty",
    "message": "I'll have your first briefing ready tomorrow morning once I've seen a full day of your workspace.",
    "id": null,
    "actions": []
  },
  "risks": {
    "status": "empty",
    "message": "No open risks. I'm watching runway, deadlines, and pipeline for you.",
    "items": []
  },
  "opportunities": {
    "status": "off",
    "message": null,
    "items": []
  }
}
```

**Trap:** `status: "off"` is a new value on the three briefing blocks (alongside `ready`, `generating`, `empty`,
`restricted`). Render it as a quiet "Daily briefing is off. Turn it on in Settings → AI preferences" state, never as an error.

## UX the FE must surface

- Switching **Daily briefing** off hides today's already-generated briefing too. Say so in the helper text.
- **Tone** does not rewrite earlier messages. Say "Applies to new replies".
- Don't show the memory section; don't add a placeholder for it.

## Verification table

| Behaviour | How verified |
|---|---|
| GET defaults, PATCH tone / toggles, the three 422s | live journey (captures above) |
| Dashboard briefing blocks `off` after switching off, back on after | live journey |
| Briefing generation job not enqueued while off, enqueued when back on | unit tests (`tests/api/test_ai_settings.py`) |
| Per-member isolation, mentor 403 | unit tests |
| Suggestions off hides actions + opportunities, keeps risks and prose | unit tests (a ready briefing with items) |
| Notifications skip opted-out members (briefing ready, course recommended) | unit tests |
| Tone sentence present in the chat and briefing prompts; unknown value falls back to default; briefing uses the founder's tone | unit tests |
| **The model actually sounding different** | **NOT verified**: the live journey runs the stub LLM. Only the prompt content is proven, not the real model's output. |
