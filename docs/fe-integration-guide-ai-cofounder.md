# FE Integration Guide — AI Co-Founder (Module 03): Consolidated Index

> **What this document is.** An **index**, not a payload guide. The AI Co-Founder shipped as a
> set of background AI-enrichment features, each embedded in a different product surface, so each
> one is documented in that surface's own FE guide. This file maps the whole Co-Founder in one
> place and points you to the authoritative section for each piece. **Every request/response body
> lives in the linked guide and was captured against the running app there**, except §5 (the chat
> orchestrator), whose bodies are pasted here from `e2e/_captures/ai_orchestrator/`.
>
> **Provenance of the two load-bearing claims in this index** (everything else is a pointer):
> - The "documented in" links were each opened and the named section confirmed present (not
>   assumed from the filename).
> - The chat is built: see §3 and §5 (the orchestrator, 2026-11-05).

---

## 0. The one thing the FE must know

"AI Co-Founder" in the product today is **not a chatbot you send messages to.** It is a brand
applied to ~8 places where the backend quietly upgrades otherwise-templated content into
AI-personalized content, plus one status endpoint. **Update (2026-10-17): a two-way conversation
backend now exists in v1** — see §3 and [ai-cofounder-chat](fe-integration-guide-ai-cofounder-chat.md).
The ~8 enrichment features below are unchanged; the interactive chat is the new piece.

Everything that *is* built follows one of two async shapes, described in §1.

---

## 1. The two async patterns (shared across every Co-Founder feature)

Nothing the Co-Founder does is instant-with-the-final-answer. Each feature either returns a
**templated placeholder first** and upgrades it, or returns **202 and makes you poll**. Learn
these two once and every row in §2 is familiar.

**Pattern A — "templated now, AI shortly after" (no new endpoint, no 202).**
The triggering call (completing an assessment, saving onboarding state, loading the dashboard)
returns a fast, deterministic **templated** value immediately, and enqueues a background job. The
FE shows the templated value right away, then **re-fetches the same read endpoint a few seconds
later** (or on the next screen visit) to pick up the AI version. There is no push and usually no
explicit status flag — you detect the upgrade by the field's content changing, except the
dashboard briefing, which carries an explicit `generating → complete` status.

**Pattern B — "202 + poll".**
The trigger returns **202** with a `status: "generating"` (or an equivalent), and the FE polls a
read endpoint until `status === "complete"` (or `"failed"`). Used by the business plan generator
and the canvas/records AI-fill.

**When the AI silently doesn't upgrade:** it may be budget exhaustion, not a bug. The workspace's
AI budget state is exposed by `GET /ai/status` (`over_budget`) — see
[ai-status](fe-integration-guide-ai-status.md). On `over_budget`, Pattern-A features keep the
templated value and Pattern-B features resolve to `failed`. Surface a graceful "AI unavailable
right now" rather than polling forever.

---

## 2. The Co-Founder, surface by surface

Each row is one shipped Co-Founder feature (Module 03 slice or the §08.11 generator built on the
same seam). Read the linked guide/section for the real bodies, status codes, and error shapes.

| # | Co-Founder feature | Where it appears for the user | Endpoint(s) the FE calls | Async job | Pattern | Documented in |
|---|---|---|---|---|---|---|
| 1 | **Assessment narrative** | Assessment results screen — the written read of their business | `POST /assessments/{id}/complete` (templated), re-fetch `GET /assessments/{id}` (AI) | `ai.assessment.narrative` | A | [ai-assessment-narrative](fe-integration-guide-ai-assessment-narrative.md) — §1 nesting trap, §2 captures |
| 2 | **Canvas AI-fill** | Business Builder canvases — "fill this for me" | `POST /business-builder/canvases/{type}/ai-fill`, then poll job or re-fetch `GET /canvases/{type}` | `business.canvas.ai_fill` | B | [ai-canvas-fill](fe-integration-guide-ai-canvas-fill.md) — §1–§4 |
| 3 | **Records AI-fill** | Business Builder typed records (persona / revenue_stream / competitor / pricing) | `POST /business-builder/{kind}/ai-fill`, then re-fetch the kind's list | `business.{kind}.ai_fill` | B | [ai-canvas-fill](fe-integration-guide-ai-canvas-fill.md) — §5 (records) |
| 4 | **Business plan generator** (§08.11) | Business Builder → full plan document | `POST /business-builder/plan/generate` → **202**, poll `GET /business-builder/plan`, then `GET /documents/{document_id}` for content | `business.plan.generate` | B | [ai-business-plan](fe-integration-guide-ai-business-plan.md) — §0–§1 |
| 5 | **Mission reason** | Today's Mission — why each task matters today | re-fetch `GET /missions/today` → `reason` | `ai.mission.reason` | A | [mission](fe-integration-guide-mission.md) — §1, `reason` subsection |
| 6 | **Health recommendations** | Health Score — the recommendation `body` copy | re-fetch `GET /health-score` → `recommendations[].body` | `ai.health.recommendations` | A | [health-score](fe-integration-guide-health-score.md) — §5, `body` subsection |
| 7 | **Dashboard daily briefing** | Founder Dashboard — "Your AI Briefing" (briefing / risks / opportunities) | re-fetch `GET /dashboard/summary` → `briefing`/`risks`/`opportunities` (explicit `generating → complete`) | `ai.dashboard.briefing` | A (with status flag) | [dashboard](fe-integration-guide-dashboard.md) — "AI daily briefing" section |
| 8 | **Roadmap re-plan rationale** | Roadmap re-plan — the AI-authored explanation of the new plan | `POST /roadmap/replan/preview` · `POST /roadmap/replan/apply` · `GET /roadmap/replan/history` → `rationale` | `ai.roadmap.rationale` | A | [roadmap](fe-integration-guide-roadmap.md) — §9 "AI Re-plan", `rationale` subsection |
| 9 | **Onboarding AI panel** | Onboarding — the one-time personalized welcome panel | re-fetch `GET /onboarding/state` → `ai_panel` | `ai.onboarding.panel` | A | [onboarding-ai-panel](fe-integration-guide-onboarding-ai-panel.md) — §1–§2 |
| — | **AI budget / status** | Any AI surface — "is AI available / how many credits left" | `GET /ai/status` | (read-only) | — | [ai-status](fe-integration-guide-ai-status.md) — §2–§3 |

> Business Builder suggestions / positioning map (`POST /business-builder/suggestions`, …) are a
> related Builder feature but are **Module 08**, not an AI Co-Founder job — documented in
> [business-builder-suggestions](fe-integration-guide-business-builder-suggestions.md). Listed here
> only so you don't confuse it with #2/#3.

---

## 3. Interactive chat (`/ai` drawer)

Built. Conversations, messages and the routed specialist replies are documented in
[ai-cofounder-chat](fe-integration-guide-ai-cofounder-chat.md) (the base shapes, Pattern B: 202, then
poll), [ai-conversations](fe-integration-guide-ai-conversations.md) (search, topic, archive, delete)
and §5 below (orchestrator, agents, Cancel, disclaimers). Still **not built**: character streaming
(replaced by polling plus Cancel, D-AI-2), action chips (add task, update roadmap, draft document),
feedback and regenerate, Suggested actions, the Bench list, memory, attachments and slash commands.

---

## 4. Verification table

| Claim in this index | Verified how |
|---|---|
| Each §2 feature's bodies/status codes/errors are real | In the linked guide — every one was captured live against the running app (see each guide's own verification table) |
| The "documented in" section exists in each linked guide | Each target section was opened and confirmed present (not inferred from filename) |
| Chat orchestrator (§5) | Live journey `e2e/test_ai_cofounder_orchestrator.py`; see §5.9 for what was faked |
| The §2 endpoint paths and async job names | Taken from the linked guides and the module build record; the FE should still treat the linked guide as the source of truth for exact shapes |

> This index re-derives no payloads; it is safe to trust exactly as far as the guides it links. If
> a linked guide changes, update the one-line description here in the same pass.

---

## 5. Chat orchestrator (slice 2, 2026-11-05)

Bodies below are pasted from live captures (`e2e/_captures/ai_orchestrator/`) against a running API plus
the in-process worker. **FAKED: the model.** `LLM_PROVIDER=stub`, so every reply text is
`[stub-llm] AI-generated assessment narrative.`. Routing, sources, disclaimers, statuses and access are
real; answer quality and a real model's latency are not exercised. Replies below show the assistant
message only (the user message and conversation fields are trimmed; nothing is edited).

### 5.1 What happens when the founder sends a message
The 201 / 202 still returns a `generating` assistant turn. The worker then **routes** the message to one of
four specialists, builds context from the workspace, and fills the turn. Poll `GET /ai/conversations/{id}`
exactly as before (about every 1.5 s). New fields on **every** message (null on user turns and while generating):

| Field | Meaning |
|---|---|
| `agent` | `{"key","name"}` of the specialist: `strategist` Strategist, `finance` Finance Advisor, `legal` Legal Assistant, `mentor` Mentor. Show "{name} via Co-Founder". |
| `reasoning_summary` | One sentence for the "Why I said this" expander: who answered, why, and which data it used. Built by the server, not the model. |
| `sources` | `[{"key","label"}]` workspace data the specialist actually read ("Your roadmap"...). Only sections with data appear. Feeds "Sources I used". No links yet. |
| `disclaimer` | Fixed legal or financial notice, set for the Finance Advisor and the Legal Assistant, else null. **Render it as its own block under the reply**; it is never inside `content`. |

New `status` value: `cancelled` (see 5.4). Statuses are now `generating | ready | failed | cancelled`.
The conversation's `topic` (`strategy | finance | legal | mentoring`) is set when the first reply is routed, so it is
null until the worker has picked the message up; it does not change afterwards.

```json
{
  "data": {
    "id": "40bb03ff-c944-485b-8e36-1795b1bda8f5",
    "title": "How long is my runway?",
    "topic": null,
    "archived_at": null,
    "created_at": "2026-11-05T21:34:44.978630+00:00",
    "updated_at": "2026-11-05T21:34:44.993424+00:00",
    "messages": [
      {
        "id": "dd65be0d-ea5c-4920-babe-87062fc3378c",
        "role": "user",
        "status": "ready",
        "content": "How long is my runway?",
        "removed_by_moderation": false,
        "agent": null,
        "reasoning_summary": null,
        "disclaimer": null,
        "sources": null,
        "created_at": "2026-11-05T21:34:44.992424+00:00"
      },
      {
        "id": "e7209239-b0bd-446c-b3c0-12f6c8a720f5",
        "role": "assistant",
        "status": "generating",
        "content": "",
        "removed_by_moderation": false,
        "agent": null,
        "reasoning_summary": null,
        "disclaimer": null,
        "sources": null,
        "created_at": "2026-11-05T21:34:44.993424+00:00"
      }
    ]
  },
  "meta": null
}
```

### 5.2 A routed reply (Finance Advisor, with disclaimer)
```json
{
  "topic": "finance",
  "assistant_message": {
    "id": "e7209239-b0bd-446c-b3c0-12f6c8a720f5",
    "role": "assistant",
    "status": "ready",
    "content": "[stub-llm] AI-generated assessment narrative.",
    "removed_by_moderation": false,
    "agent": {
      "key": "finance",
      "name": "Finance Advisor"
    },
    "reasoning_summary": "Answered by the Finance Advisor because your message is about finance. I used: Your startup profile, Your finance snapshot.",
    "disclaimer": "This is general guidance based on the figures in your workspace, not financial, tax or investment advice. Check important decisions with a qualified accountant or adviser.",
    "sources": [
      {
        "key": "profile",
        "label": "Your startup profile"
      },
      {
        "key": "finance",
        "label": "Your finance snapshot"
      }
    ],
    "created_at": "2026-11-05T21:34:44.993424+00:00"
  }
}
```

### 5.3 The other specialists
Strategist (used the roadmap and today's mission because the workspace had them):
```json
{
  "topic": "strategy",
  "assistant_message": {
    "id": "44501724-60de-4a33-bff2-a9c03a551919",
    "role": "assistant",
    "status": "ready",
    "content": "[stub-llm] AI-generated assessment narrative.",
    "removed_by_moderation": false,
    "agent": {
      "key": "strategist",
      "name": "Strategist"
    },
    "reasoning_summary": "Answered by the Strategist because your message is about strategy. I used: Your startup profile, Your roadmap, Today's mission.",
    "disclaimer": null,
    "sources": [
      {
        "key": "profile",
        "label": "Your startup profile"
      },
      {
        "key": "roadmap",
        "label": "Your roadmap"
      },
      {
        "key": "mission",
        "label": "Today's mission"
      }
    ],
    "created_at": "2026-11-05T21:34:45.066646+00:00"
  }
}
```
Legal Assistant (note the disclaimer; the Mentor and the Strategist have `"disclaimer": null`):
```json
{
  "topic": "legal",
  "assistant_message": {
    "id": "bedf9570-7236-43bd-b12c-f8dd3857d631",
    "role": "assistant",
    "status": "ready",
    "content": "[stub-llm] AI-generated assessment narrative.",
    "removed_by_moderation": false,
    "agent": {
      "key": "legal",
      "name": "Legal Assistant"
    },
    "reasoning_summary": "Answered by the Legal Assistant because your message is about legal. I used: Your startup profile.",
    "disclaimer": "I'm an AI, not a lawyer. This is general information, not legal advice. Have a qualified lawyer review anything before you rely on it or sign it.",
    "sources": [
      {
        "key": "profile",
        "label": "Your startup profile"
      }
    ],
    "created_at": "2026-11-05T21:34:45.024823+00:00"
  }
}
```
Mentor:
```json
{
  "topic": "mentoring",
  "assistant_message": {
    "id": "0cbf9d2e-d21e-4a63-be82-35c5230e727e",
    "role": "assistant",
    "status": "ready",
    "content": "[stub-llm] AI-generated assessment narrative.",
    "removed_by_moderation": false,
    "agent": {
      "key": "mentor",
      "name": "Mentor"
    },
    "reasoning_summary": "Answered by the Mentor because your message is about mentoring. I used: Your startup profile, Today's mission, Your roadmap.",
    "disclaimer": null,
    "sources": [
      {
        "key": "profile",
        "label": "Your startup profile"
      },
      {
        "key": "mission",
        "label": "Today's mission"
      },
      {
        "key": "roadmap",
        "label": "Your roadmap"
      }
    ],
    "created_at": "2026-11-05T21:34:45.045116+00:00"
  }
}
```
**Routing rules (for copy and expectations):** clear keywords decide it with no model spend; a vague follow-up
("and next month?") stays with the last specialist; only a vague first message costs a small routing call; if
that fails or the budget is gone, the Strategist answers. Topic filter on the list:
```json
{
  "data": [
    {
      "id": "842a9cb4-0701-45e8-b730-3dd09fe466c0",
      "title": "Draft an NDA for a contractor",
      "topic": "legal",
      "archived_at": null,
      "created_at": "2026-11-05T21:34:45.015401+00:00",
      "updated_at": "2026-11-05T21:34:46.868944+00:00"
    }
  ],
  "meta": null
}
```

### 5.4 Cancel (replaces "Stop generating")
`POST /ai/conversations/{id}/messages/{message_id}/cancel`, no body, `message_id` is the **assistant** message.
Works while `generating`; idempotent (a second call is 200). The turn becomes `cancelled` with empty `content`,
the worker discards any reply it was producing, and the conversation accepts a new message at once.
Tokens already spent are still counted against the daily budget.
```json
{
  "data": {
    "id": "8cb02003-96df-42da-9483-c6f97233393d",
    "role": "assistant",
    "status": "cancelled",
    "content": "",
    "removed_by_moderation": false,
    "agent": null,
    "reasoning_summary": null,
    "disclaimer": null,
    "sources": null,
    "created_at": "2026-11-05T21:34:47.165741+00:00"
  },
  "meta": null
}
```
Cancel after the reply finished:
```json
{
  "error": {
    "code": "REPLY_NOT_RUNNING",
    "message": "This reply has already finished.",
    "field_errors": []
  }
}
```
Another member's conversation, or a message id that is not an assistant turn of this conversation: `404`
`NOT_FOUND` (same body as below). Mentor and accountant:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
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

### 5.5 Follow-up message
Unchanged shape, now with the new null fields:
```json
{
  "data": {
    "id": "1b86a915-b3be-4c82-86d5-c08bb2a44d51",
    "role": "assistant",
    "status": "generating",
    "content": "",
    "removed_by_moderation": false,
    "agent": null,
    "reasoning_summary": null,
    "disclaimer": null,
    "sources": null,
    "created_at": "2026-11-05T21:34:47.089361+00:00"
  },
  "meta": null
}
```

### 5.6 Team member
A team member gets the same specialists with the same data their role may read, in their own private
conversations (the founder cannot see them, and vice versa).

### 5.7 UX the FE must surface
- Show the specialist badge from `agent`, not a fixed "Co-Founder" label.
- Render `disclaimer` as a separate muted block under the reply; do not merge it into the markdown.
- "Why I said this" shows `reasoning_summary`; "Sources I used" lists `sources[].label`.
- Replace "Stop generating" with **Cancel** while the last assistant turn is `generating`; on `cancelled` show
  "Cancelled" and enable the composer. A `409 REPLY_NOT_RUNNING` means the reply just finished: refresh instead.
- Treat unknown `status` values defensively; `cancelled` is new.
- The reply can fail for budget: unchanged (`failed` with the daily-limit text).

### 5.8 Events (for Analytics, nothing for the FE)
`ai.cofounder.reply.completed` (message, conversation, workspace, agent, routing method) and
`ai.cofounder.reply.cancelled`. `ai.cofounder.reply.failed` stays without a notification (the FE already shows it).
`ai.action.executed` and `ai.suggestion.created` arrive with actions and suggestions (next slice).

### 5.9 Verification table (slice 2)
| Behaviour | Status |
|---|---|
| Routing to all four agents by rules; carryover follow-up; topic filter | verified live (stub model) |
| Router model call for vague first messages | verified live only as the stub's fixed pick (always `finance`); unit-tested for valid, invalid and failing answers |
| Sources (profile, roadmap, mission), disclaimers, `reasoning_summary` text | verified live |
| Cancel before the worker runs; 409 after done; 404 and 403 shapes | verified live |
| Cancel racing a running model call | unit-tested only |
| Retry never re-pays for routing; failed calls billed; over-budget path | unit-tested only |
| Context for pipeline, canvas, health, assessment sections | unit-tested only (the live workspace had none) |
| Real model answer quality, latency, token cost per reply | not verified (stub) |

---

## 6. Slice 3: actions, feedback, regenerate, suggestions, bench, pinning (2026-11-06)

Bodies are pasted from live captures (`e2e/_captures/ai_slice3/`, `e2e/test_ai_cofounder_slice3.py`).
**FAKED: the model.** The worker's LLM client is scripted by the test to return a reply containing an
`<actions>` block, so the *proposals* are test-written; everything the API does with them is real. No real
model has proposed an action yet (quality and how often it does are not verified).

**Shape changes since §5 (the §5 bodies pre-date these keys):** every message now also has
`"actions": []` and `"feedback": null`; every conversation has `"pinned_agent_key": null`. They are always
present. `POST /conversations` accepts an optional `agent_key`.

### 6.1 Proposed actions ("Add as task", "Update roadmap", "Draft the document")
A reply may carry `actions` (at most 3). **Nothing is changed until the founder confirms.** The model never
supplies ids; milestones are matched server-side.
```json
{
  "assistant_message": {
    "id": "89360176-2515-4f64-a535-d54810b90585",
    "role": "assistant",
    "status": "ready",
    "content": "Here is my take.",
    "removed_by_moderation": false,
    "agent": {
      "key": "strategist",
      "name": "Strategist"
    },
    "reasoning_summary": "Answered by the Strategist because your message is about strategy. I used: Your startup profile, Your roadmap, Today's mission.",
    "disclaimer": null,
    "sources": [
      {
        "key": "profile",
        "label": "Your startup profile"
      },
      {
        "key": "roadmap",
        "label": "Your roadmap"
      },
      {
        "key": "mission",
        "label": "Today's mission"
      }
    ],
    "actions": [
      {
        "id": "21bb6cbd-2a06-480f-b34f-ad9ce21015b5",
        "kind": "add_task",
        "label": "Add as task: Call five customers",
        "status": "proposed",
        "undoable": false,
        "params": {
          "title": "Call five customers",
          "effort": "small",
          "milestone_key": "M1"
        },
        "href": null,
        "result_label": null
      },
      {
        "id": "f893f26c-491f-4cf9-b628-d0f60aa60954",
        "kind": "update_roadmap",
        "label": "Update roadmap (M2): status in_progress, due 2027-03-01",
        "status": "proposed",
        "undoable": false,
        "params": {
          "due_on": "2027-03-01",
          "status": "in_progress",
          "milestone_key": "M2"
        },
        "href": null,
        "result_label": null
      },
      {
        "id": "49290d94-3564-4bd7-bff5-6be713a70d8d",
        "kind": "draft_document",
        "label": "Draft the document: Contractor NDA",
        "status": "proposed",
        "undoable": false,
        "params": {
          "title": "Contractor NDA",
          "doc_kind": "nda",
          "sections": [
            {
              "body": "NovaPay and the contractor.",
              "heading": "Parties"
            }
          ]
        },
        "href": null,
        "result_label": null
      }
    ],
    "feedback": null,
    "created_at": "2026-11-06T19:06:27.895596+00:00"
  }
}
```
Action fields: `kind` (`add_task | update_roadmap | draft_document`), `label` (button text), `status`
(`proposed | executed | dismissed | undone | failed`), `undoable` (true only when `executed`), `params`
(for display), `href` and `result_label` (set after confirm: "Open it").

`POST /ai/messages/{message_id}/actions/{action_id}/confirm` runs it. Idempotent when repeated.
```json
{
  "data": {
    "id": "21bb6cbd-2a06-480f-b34f-ad9ce21015b5",
    "kind": "add_task",
    "label": "Add as task: Call five customers",
    "status": "executed",
    "undoable": true,
    "params": {
      "title": "Call five customers",
      "effort": "small",
      "milestone_key": "M1"
    },
    "href": "/roadmap",
    "result_label": "Task added to Write your problem statement"
  },
  "meta": null
}
```
```json
{
  "data": {
    "id": "49290d94-3564-4bd7-bff5-6be713a70d8d",
    "kind": "draft_document",
    "label": "Draft the document: Contractor NDA",
    "status": "executed",
    "undoable": true,
    "params": {
      "title": "Contractor NDA",
      "doc_kind": "nda",
      "sections": [
        {
          "body": "NovaPay and the contractor.",
          "heading": "Parties"
        }
      ]
    },
    "href": "/documents/11065948-f069-4c37-9e91-4a18dc632113",
    "result_label": "Draft created: Contractor NDA"
  },
  "meta": null
}
```
`.../undo` reverses it (`status` becomes `undone`):
```json
{
  "data": {
    "id": "21bb6cbd-2a06-480f-b34f-ad9ce21015b5",
    "kind": "add_task",
    "label": "Add as task: Call five customers",
    "status": "undone",
    "undoable": false,
    "params": {
      "title": "Call five customers",
      "effort": "small",
      "milestone_key": "M1"
    },
    "href": "/roadmap",
    "result_label": "Task added to Write your problem statement"
  },
  "meta": null
}
```
After an undo, confirming again is refused:
```json
{
  "error": {
    "code": "ACTION_NOT_PROPOSED",
    "message": "This action is already undone.",
    "field_errors": []
  }
}
```
`.../dismiss` closes a proposal:
```json
{
  "data": {
    "id": "1566350e-3930-4c6c-8c19-dadc5c0817be",
    "kind": "add_task",
    "label": "Add as task: Write the pitch",
    "status": "dismissed",
    "undoable": false,
    "params": {
      "title": "Write the pitch",
      "effort": "medium",
      "milestone_key": "M1"
    },
    "href": null,
    "result_label": null
  },
  "meta": null
}
```
Unknown action id (or someone else's message): 
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
Other errors (unit/API-tested, not captured live): `409 ACTION_NOT_PROPOSED` (dismissed/undone), `409 ACTION_TARGET_GONE`
(the milestone was deleted meanwhile), `409 ACTION_CHANGED` on undo (the task, milestone or value was edited
since, so nothing is deleted), `403` for mentor/accountant.
Rules: `update_roadmap` only proposes `todo`/`in_progress` (never `done`); a drafted NDA or contractor agreement
ends with a "Not legal advice" section; undoing a document moves it to the Documents trash (recoverable).

### 6.2 Feedback
`POST /ai/messages/{message_id}/feedback` `{"rating":"up"|"down"|null,"note":"<=500 chars"}` (null clears). Only on
your own finished reply (`409 FEEDBACK_NOT_ALLOWED` while generating).
```json
{
  "data": {
    "id": "0a7ab72b-44b0-44a6-ba56-f6702ce9d58d",
    "role": "assistant",
    "status": "ready",
    "content": "Another idea.",
    "removed_by_moderation": false,
    "agent": {
      "key": "strategist",
      "name": "Strategist"
    },
    "reasoning_summary": "Answered by the Strategist because your message is about strategy. I used: Your startup profile, Your roadmap, Today's mission.",
    "disclaimer": null,
    "sources": [
      {
        "key": "profile",
        "label": "Your startup profile"
      },
      {
        "key": "roadmap",
        "label": "Your roadmap"
      },
      {
        "key": "mission",
        "label": "Today's mission"
      }
    ],
    "actions": [
      {
        "id": "1566350e-3930-4c6c-8c19-dadc5c0817be",
        "kind": "add_task",
        "label": "Add as task: Write the pitch",
        "status": "dismissed",
        "undoable": false,
        "params": {
          "title": "Write the pitch",
          "effort": "medium",
          "milestone_key": "M1"
        },
        "href": null,
        "result_label": null
      }
    ],
    "feedback": "down",
    "created_at": "2026-11-06T19:06:28.299290+00:00"
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "rating",
        "message": "Input should be 'up' or 'down'"
      }
    ]
  }
}
```

### 6.3 Regenerate / Retry
`POST /ai/conversations/{id}/messages/{message_id}/regenerate` (202) answers the **latest** reply again. It reuses
the same turn (same `id`), keeps the same specialist, drops unconfirmed proposals, and is billed like a new
message. It is also the Retry for a failed or cancelled reply. Poll the conversation as usual.
```json
{
  "data": {
    "id": "0a7ab72b-44b0-44a6-ba56-f6702ce9d58d",
    "role": "assistant",
    "status": "generating",
    "content": "",
    "removed_by_moderation": false,
    "agent": null,
    "reasoning_summary": null,
    "disclaimer": null,
    "sources": null,
    "actions": [],
    "feedback": null,
    "created_at": "2026-11-06T19:06:28.417957+00:00"
  },
  "meta": null
}
```
Refused with `409`: `NOT_LATEST_REPLY`, `REPLY_IN_PROGRESS`, `ACTION_EXECUTED` (the reply already changed something;
ask a new question), `CONVERSATION_ARCHIVED`. Captured:
```json
{
  "error": {
    "code": "ACTION_EXECUTED",
    "message": "This reply already made a change. Ask a new question instead.",
    "field_errors": []
  }
}
```
Over the daily limit the regenerated turn ends `failed` with the usual limit text.

### 6.4 Suggested actions
`GET /ai/suggestions` returns cards built from what the app already generates (today's briefing actions and
opportunities, pending health recommendations, ready course recommendations): no new model spend. Cards are
per member. With "Proactive suggestions" off the response is `{"enabled": false, "items": []}`. Before the
assessment there is usually nothing:
```json
{
  "data": {
    "enabled": true,
    "items": []
  },
  "meta": null
}
```
After the assessment:
```json
{
  "data": {
    "enabled": true,
    "items": [
      {
        "id": "49ac3dda-bf13-49ab-bf34-e76b64b1879c",
        "source": "health",
        "title": "Get your key agreements signed",
        "body": "Send your founders' agreement or first customer contract for e-signature and get it fully signed.",
        "href": "/health",
        "status": "new",
        "created_at": "2026-11-06T19:06:29.074604+00:00"
      },
      {
        "id": "5ce5ffa6-589f-44e4-8476-ea498afdb7b0",
        "source": "health",
        "title": "Close out your next roadmap tasks",
        "body": "Finish the open tasks on your nearest milestone. Shipped work is the clearest product signal.",
        "href": "/health",
        "status": "new",
        "created_at": "2026-11-06T19:06:29.074604+00:00"
      },
      {
        "id": "625ccd7e-bdcd-403c-b842-efc4e973102b",
        "source": "health",
        "title": "Track your money in and out",
        "body": "Record this month's income and expenses so your runway and burn are based on real numbers.",
        "href": "/health",
        "status": "new",
        "created_at": "2026-11-06T19:06:29.074604+00:00"
      }
    ]
  },
  "meta": null
}
```
`href` is a fixed app route; "Do it" = `POST .../suggestions/{id}/accept` then navigate to `href`.
```json
{
  "data": {
    "id": "49ac3dda-bf13-49ab-bf34-e76b64b1879c",
    "source": "health",
    "title": "Get your key agreements signed",
    "body": "Send your founders' agreement or first customer contract for e-signature and get it fully signed.",
    "href": "/health",
    "status": "accepted",
    "created_at": "2026-11-06T19:06:29.074604+00:00"
  },
  "meta": null
}
```
`.../dismiss`, and `.../snooze` `{"days":1..30}` (default 7; a snoozed card returns after that time):
```json
{
  "data": {
    "id": "625ccd7e-bdcd-403c-b842-efc4e973102b",
    "source": "health",
    "title": "Track your money in and out",
    "body": "Record this month's income and expenses so your runway and burn are based on real numbers.",
    "href": "/health",
    "status": "snoozed",
    "created_at": "2026-11-06T19:06:29.074604+00:00"
  },
  "meta": null
}
```
Accepted or dismissed cards never come back; accepting a dismissed one is `409 SUGGESTION_CLOSED`.
A new batch also creates one in-app notification (digest-eligible, category "AI & insights"):
```json
{
  "data": {
    "notifications": [
      {
        "id": "c0380fe7-11f2-47cd-9e26-c84a069dba56",
        "type": "ai.suggestion.created",
        "title": "Your AI Co-Founder has a suggestion for you",
        "body": "See suggested actions",
        "data": {
          "notify": true,
          "source": "health",
          "user_id": "ef68e558-a4e9-4ceb-9be2-407fc0f5818f",
          "startup_id": "3d5c5a58-2dee-4e8a-9168-edccf1d40f82",
          "suggestion_id": "625ccd7e-bdcd-403c-b842-efc4e973102b"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "ai",
        "created_at": "2026-11-06T19:06:29.074604+00:00"
      },
      {
        "id": "8f5006e5-82c0-4e2b-a107-b2a8535e643a",
        "type": "notification.onboarding_complete",
        "title": "Your workspace is ready",
        "body": "Your roadmap and Health Score are being set up. Take the Kickoff Assessment to sharpen them.",
        "data": {
          "user_id": "ef68e558-a4e9-4ceb-9be2-407fc0f5818f",
          "startup_id": "3d5c5a58-2dee-4e8a-9168-edccf1d40f82"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "other",
        "created_at": "2026-11-06T19:06:27.787904+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

### 6.5 The bench and pinning
`GET /ai/agents`: 11 entries; four `available: true` (strategist, finance, legal, mentor), seven
`status: "coming_soon"`. Names and one-liners for the seven come from the FE bench list (the PRD table is not in
the PRD file, so unverified against it).
```json
{
  "data": [
    {
      "key": "strategist",
      "name": "Strategist",
      "category": "Business Builder",
      "description": "Sharpens your model, plan, and positioning.",
      "topic": "strategy",
      "available": true,
      "status": "available"
    },
    {
      "key": "finance",
      "name": "Finance Advisor",
      "category": "Finance Hub",
      "description": "Runway, forecasts, and the hard money calls.",
      "topic": "finance",
      "available": true,
      "status": "available"
    },
    {
      "key": "legal",
      "name": "Legal Assistant",
      "category": "Legal & Compliance",
      "description": "Contracts, formation, and staying covered.",
      "topic": "legal",
      "available": true,
      "status": "available"
    },
    {
      "key": "mentor",
      "name": "Mentor",
      "category": "Across everything",
      "description": "A steady voice for the founder behind the company.",
      "topic": "mentoring",
      "available": true,
      "status": "available"
    },
    {
      "key": "marketing",
      "name": "Marketing Advisor",
      "category": "Marketing Hub",
      "description": "Campaigns, copy, and channel strategy.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    },
    {
      "key": "sales",
      "name": "Sales Coach",
      "category": "Sales Hub",
      "description": "Pipeline, objections, and closing with confidence.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    },
    {
      "key": "fundraising",
      "name": "Fundraising Copilot",
      "category": "Funding Hub",
      "description": "Investors, the data room, and running the raise.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    },
    {
      "key": "readiness",
      "name": "Readiness Coach",
      "category": "Investor Readiness",
      "description": "Your deck, your story, and the tough questions.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    },
    {
      "key": "validation",
      "name": "Validation Scientist",
      "category": "Validation Hub",
      "description": "Experiments that prove it before you build.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    },
    {
      "key": "product",
      "name": "Product Manager",
      "category": "Roadmap",
      "description": "Turns feedback into the right next build.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    },
    {
      "key": "insight",
      "name": "Insight Synthesizer",
      "category": "Validation Hub",
      "description": "Finds the patterns hiding in your feedback.",
      "topic": null,
      "available": false,
      "status": "coming_soon"
    }
  ],
  "meta": null
}
```
"Ask {agent}": send `agent_key` (an available key) on `POST /conversations`; every reply there goes to that
specialist (`reasoning_summary` says "you asked for this specialist"). A coming-soon key is `422`.
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That specialist isn't available yet.",
    "field_errors": []
  }
}
```

### 6.6 UX the FE must surface
- Show the confirm chips; after confirm show `result_label` with an **Open it** link (`href`) and **Undo**
  while `undoable`. Handle `ACTION_CHANGED` with "This was edited since, so it can't be undone automatically."
- Hide Regenerate on any reply with an executed action; show **Retry** on `failed`/`cancelled`.
- Thumbs are private to the member; show the current `feedback`.
- Suggested actions and Your bench tabs can be un-hidden now (D-AI-6).

### 6.7 Verification table (slice 3)
| Behaviour | Status |
|---|---|
| Proposals saved, confirm / undo / dismiss for all three kinds, document appears in Documents | verified live (model scripted) |
| Feedback, regenerate, pinning, bench, suggestions accept/snooze, one notification | verified live |
| Real model proposing actions, parse robustness on real output | NOT verified |
| `ACTION_CHANGED` / `ACTION_TARGET_GONE` / role 403 / moderation-removed message | API-tested only |
| Confirm racing regenerate; stale job after Cancel then Regenerate | unit/API-tested only (single connection) |
| Suggestions from briefing opportunities and learning recommendations | API-tested only (live workspace had health cards only) |
