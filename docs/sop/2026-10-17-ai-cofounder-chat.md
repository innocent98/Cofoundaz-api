# SOP — AI Co-Founder interactive chat (Module 03, v1 backend)

**What shipped (one line):** Built the two-way AI Co-Founder chat backend that did not exist —
conversations + messages + a worker that answers from the LLM with workspace context — so the FE
dock/`/ai`/"Tell me more" can stop saying "coming soon." Branch `fix/ai-cofounder-chat` → PR to
`develop`.

## Why
The AI Co-Founder's headline feature (the chat) had **no backend**: the `/ai` router exposed only
`GET /ai/status` (see `docs/fe-integration-guide-ai-cofounder.md` §3). It's the product's namesake
and the most visible "coming soon," so it's the highest-impact gap to close.

## How (v1 scope + approach)
- **Pattern B (202 → poll)**, reusing the existing LLM + budget + worker seam — no new streaming
  infrastructure. `POST …/messages` stores the founder's turn + an empty `generating` assistant
  turn and enqueues `ai.cofounder.reply`; the worker calls the LLM and fills the turn; the FE polls.
- **Workspace context**: the prompt includes the startup's name/industry/stage/one-liner/goals so
  answers are specific (verified live — it answered a gig-worker fintech startup with a concrete
  MVP-narrowing plan). Business signals only, no PII.
- **One co-founder voice** in v1. **Over budget → assistant `failed`** (a chat turn has no
  templated fallback). Message order made deterministic (explicit `created_at` gap; both turns
  otherwise share the transaction's `now()`).
- **Security hardening** (from the commit security review): chat routes are gated to
  founder/team_member (`require_role`); conversations are **private to their creator**
  (`created_by`), not workspace-shared; a new message is refused with **409 `REPLY_IN_PROGRESS`**
  while an assistant turn is still `generating`; and the worker caps history to the last 20 turns
  to bound per-reply LLM input cost.
- **Deferred to later slices** (kept out on purpose): SSE token streaming, per-specialist routing
  badges, action chips/tool-results, proactive suggestions, long-term memory, AI settings.

## What's involved
- Models: `app/db/models/ai_chat.py` (`AiConversation`, `AiMessage`) + enums `ChatMessageRole`,
  `ChatMessageStatus`; registered in `app/db/models/__init__.py`.
- Migration: `alembic/versions/0047_ai_chat.py` (two new tables).
- Service: `app/services/ai_cofounder/{service,prompts}.py`.
- Endpoints: added to `app/api/v1/endpoints/ai.py` — `POST/GET /ai/conversations`,
  `GET /ai/conversations/{id}`, `POST /ai/conversations/{id}/messages`.
- Worker: `handle_cofounder_reply` + `register_handler("ai.cofounder.reply", …)` in
  `app/worker/handlers/ai.py`.
- Schemas: `app/schemas/ai_chat.py`. Docs: `docs/fe-integration-guide-ai-cofounder-chat.md`.
- Tests: `tests/api/test_ai_chat.py`, `tests/worker/test_cofounder_reply_handler.py`.

## Verification
- **Live** (local full-stack, real `gpt-5.6-luna`): created a conversation, polled, got a real
  context-aware answer; message order + statuses correct. Captures in the FE guide.
- **Gates:** ruff/black/isort/mypy clean on the changed set; full `pytest` + coverage in the PR.

## Operate / roll back
Pure additive (two new tables, new routes, new job type). `alembic downgrade -1` drops the tables.
No existing behaviour changes.

## Follow-ups
- **FE:** wire the dock drawer + `/ai` page + dashboard "Tell me more" to these endpoints (separate
  slice); update `fe-integration-guide-ai-cofounder.md` §3 ("chat NOT built") once this lands.
- **MIGRATION-HEAD COORDINATION:** `0047` was reserved for Victoria's Module 13 — whichever lands
  on `develop` second must rebase its `down_revision` onto the first (single-head rule).
- Later slices: streaming, specialist routing, action chips, suggestions, memory.
