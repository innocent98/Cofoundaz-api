# FE Integration Guide: AI Co-Founder conversations (slice 1)

Every body below is pasted from a **live capture** against a running API + in-process worker on
2026-11-05 (`e2e/test_ai_conversations_slice1.py`, files in `e2e/_captures/ai_conversations/`).
**What was faked:** `LLM_PROVIDER=stub`, so every assistant reply is the fixed string
`[stub-llm] AI-generated assessment narrative.`; a real model's text will differ, the shapes will not.
Base path `/api/v1/ai`; Bearer token + `X-Workspace-Id` on every call; founder and team member only
(mentor and accountant get `403`, including on `/ai/status`).

## 1. What changed for the FE

| Before | Now |
|---|---|
| `POST /conversations` with no message created an empty conversation | `message` is **required**; blank or whitespace is `422` |
| `POST .../messages` accepted `"   "` | stripped, then `422` if empty |
| `GET /conversations` ignored `query` and `topic` | both work; `archived=true` lists the archive |
| No way to remove a conversation | bulk archive / unarchive and `DELETE` ("Delete forever") |
| `GET /ai/status` open to every member | founder + team member only |

## 2. Endpoints

### POST `/conversations` (201)
Body `{"message": "…"}` (trimmed; 1 to 8000 chars). The first assistant turn is `generating`; poll
`GET /conversations/{id}` as before.
```json
{
  "data": {
    "id": "d6a4b974-da79-4a5e-b3be-71b37be0bf42",
    "title": "Draft an NDA for a contractor",
    "topic": null,
    "archived_at": null,
    "created_at": "2026-11-05T21:22:50.763436+00:00",
    "updated_at": "2026-11-05T21:22:50.777049+00:00",
    "messages": [
      {
        "id": "ee91e1ae-7972-452e-b0a7-bc8edde00e31",
        "role": "user",
        "status": "ready",
        "content": "Draft an NDA for a contractor",
        "removed_by_moderation": false,
        "created_at": "2026-11-05T21:22:50.776049+00:00"
      },
      {
        "id": "cd40c58a-c0f2-4b21-8c11-12d87b94fda9",
        "role": "assistant",
        "status": "generating",
        "content": "",
        "removed_by_moderation": false,
        "created_at": "2026-11-05T21:22:50.777049+00:00"
      }
    ]
  },
  "meta": null
}
```
Errors (field name is the body key, **not** prefixed):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "message",
        "message": "Field required"
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
        "field": "message",
        "message": "String should have at least 1 character"
      }
    ]
  }
}
```

### POST `/conversations/{id}/messages` (202)
`content` is trimmed first. Blank:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "content",
        "message": "String should have at least 1 character"
      }
    ]
  }
}
```
On an archived conversation:
```json
{
  "error": {
    "code": "CONVERSATION_ARCHIVED",
    "message": "This conversation is archived. Unarchive it to continue.",
    "field_errors": []
  }
}
```
(The existing `409 REPLY_IN_PROGRESS` is unchanged. Not re-captured here.)

### GET `/conversations?query=&topic=&archived=`
| Param | Rule |
|---|---|
| `query` | 2 to 100 chars; case-insensitive; matches the **title** and any **ready, non-removed message** (user or assistant). `%` and `_` are literal. Adds `match_snippet` to each item (about 120 chars around the first hit, with `…` at a cut end; the title if only the title matched). Absent when no `query`. |
| `topic` | one of `strategy`, `finance`, `legal`, `mentoring`; anything else is `422`. Conversations get a topic when a reply is routed (next slice), so until then every `topic` is `null` and a topic filter returns `[]`. |
| `archived` | `true` lists only archived conversations; default `false` hides them. |

Default list (newest activity first, at most 200, only the caller's own):
```json
{
  "data": [
    {
      "id": "72d8fa87-39f4-48de-8f96-fe08c24edb41",
      "title": "How long is my runway?",
      "topic": null,
      "archived_at": null,
      "created_at": "2026-11-05T21:22:53.007368+00:00",
      "updated_at": "2026-11-05T21:22:53.016005+00:00"
    },
    {
      "id": "d6a4b974-da79-4a5e-b3be-71b37be0bf42",
      "title": "Draft an NDA for a contractor",
      "topic": null,
      "archived_at": null,
      "created_at": "2026-11-05T21:22:50.763436+00:00",
      "updated_at": "2026-11-05T21:22:50.777049+00:00"
    }
  ],
  "meta": null
}
```
Reply-text match (`match_snippet` is the reply, here the stub text):
```json
{
  "data": [
    {
      "id": "72d8fa87-39f4-48de-8f96-fe08c24edb41",
      "title": "How long is my runway?",
      "topic": null,
      "archived_at": null,
      "created_at": "2026-11-05T21:22:53.007368+00:00",
      "updated_at": "2026-11-05T21:22:53.016005+00:00",
      "match_snippet": "[stub-llm] AI-generated assessment narrative."
    },
    {
      "id": "d6a4b974-da79-4a5e-b3be-71b37be0bf42",
      "title": "Draft an NDA for a contractor",
      "topic": null,
      "archived_at": null,
      "created_at": "2026-11-05T21:22:50.763436+00:00",
      "updated_at": "2026-11-05T21:22:50.777049+00:00",
      "match_snippet": "[stub-llm] AI-generated assessment narrative."
    }
  ],
  "meta": null
}
```
Title match:
```json
{
  "data": [
    {
      "id": "72d8fa87-39f4-48de-8f96-fe08c24edb41",
      "title": "How long is my runway?",
      "topic": null,
      "archived_at": null,
      "created_at": "2026-11-05T21:22:53.007368+00:00",
      "updated_at": "2026-11-05T21:22:53.016005+00:00",
      "match_snippet": "How long is my runway?"
    }
  ],
  "meta": null
}
```
Topic filter with no tagged conversations:
```json
{
  "data": [],
  "meta": null
}
```
Archive view:
```json
{
  "data": [
    {
      "id": "d6a4b974-da79-4a5e-b3be-71b37be0bf42",
      "title": "Draft an NDA for a contractor",
      "topic": null,
      "archived_at": "2026-11-05T21:22:53.127246+00:00",
      "created_at": "2026-11-05T21:22:50.763436+00:00",
      "updated_at": "2026-11-05T21:22:53.120884+00:00"
    }
  ],
  "meta": null
}
```
Errors:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.query",
        "message": "String should have at least 2 characters"
      }
    ]
  }
}
```
**Trap:** the field name here is `query.query` (a query-string param is nested under `query`).
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "topic must be one of: strategy, finance, legal, mentoring.",
    "field_errors": []
  }
}
```
**Archived chats are not searchable (D-AI-4):**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Archived conversations can't be searched.",
    "field_errors": []
  }
}
```

### POST `/conversations/archive` and `/conversations/unarchive` (200)
Body `{"ids": ["<uuid>", …]}`, 1 to 100 ids. Idempotent: the count is how many actually changed.
**All or nothing:** one id that is not yours (or does not exist) fails the whole call with `404`
and nothing changes; the FE should only send ids from its own list.
```json
{
  "data": {
    "archived": 1
  },
  "meta": null
}
```
```json
{
  "data": {
    "unarchived": 1
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### DELETE `/conversations/{id}` (200): "Delete forever"
Works on archived or active conversations; removes the conversation and all its messages.
Irreversible: show a confirm dialog.
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```
Afterwards a GET (or a second DELETE) is:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 3. Shape changes on a conversation
Every conversation object (list, create, get) now carries `"topic": string|null` and
`"archived_at": string|null`. They are **always present** (null, not absent). `match_snippet` is the only
key that is present *only* when `query` was sent. Messages are unchanged.

## 4. UX the FE must surface
- Disable Send for whitespace-only text; the API now rejects it.
- Archiving moves a chat out of the list **and out of ⌘K search**; show it under an "Archived" filter
  with "Unarchive". Do not offer search inside the archive.
- A chat opened from the archive is read-only until unarchived (send gives `CONVERSATION_ARCHIVED`).
- "Delete forever" needs a confirmation; there is no undo.
- Mentor and accountant must not call `/ai/status` (403); the Settings usage card is F + TM only.

## 5. Verification table
| Behaviour | Status |
|---|---|
| All bodies and status codes above | verified live (stub LLM) |
| Role gate on `/ai/status`, archive, delete (403 for mentor, accountant) | API tests (`tests/api/test_ai_conversations_slice1.py`); founder `200` verified live; **403 not captured live** |
| Another member's ids (404, nothing changed) | API tests only |
| ⌘K search skipping archived chats | API test only |
| Real-model reply text in snippets | not verified (stub) |
| `topic` set on a conversation | not yet: slice 2 |
