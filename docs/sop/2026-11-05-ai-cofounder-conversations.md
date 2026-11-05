# AI Co-Founder: conversation management (slice 1)

**What shipped:** gap-list rows 12, 13, 14, 10 (`query` + `topic`) and 17 (archive, "Delete forever") from `docs/checklist/ai-cofounder-prd-gaps.md` (FE repo). Branch `feature/ai-cofounder-slice-1`, migration **`0097_ai_conversation_archive`** on `0095_funding_data_room` (the lead re-chains). FE guide `docs/fe-integration-guide-ai-conversations.md`.

**Why:** a blank message made a model call and an empty title; an empty conversation could be created; `/ai/status` leaked token spend and failure times to mentor and accountant; list filters were ignored; and a founder had no way to remove a private conversation.

**How / decisions**

| Topic | Decision |
|---|---|
| Blank input (D-AI-9) | `StringConstraints(strip_whitespace, min_length=1)` on both bodies: 422 before any job. |
| Empty conversation (D-AI-8) | `message` is required on create; the service no longer has a no-message path. |
| Status (D-AI-7) | `/ai/status` uses the same F + TM dependency as the rest of the module. |
| Search | `query` (2 to 100 chars) is an `ILIKE` over the title and an `EXISTS` over ready, non-moderation-removed messages. `%`/`_`/`\` are escaped. Snippet is a window around the first message hit (title if only the title matched). No full-text index: lists are per user and capped at 200, so a scan is fine. |
| Archive (D-AI-4) | `archived_at` timestamp. Default list and ⌘K search hide archived; `archived=true` lists them; `archived=true` with `query` is 422 (not searchable). Posting to an archived chat is `409 CONVERSATION_ARCHIVED`. Bulk archive/unarchive take up to 100 ids, are idempotent, and are all-or-nothing: any id that is not the caller's is a 404, so ids give no existence signal. |
| Delete forever | `DELETE /conversations/{id}`; the FK cascade removes messages. An in-flight reply job finds no message and does nothing. |
| Topic | Column and filter only. Values are fixed in `app/services/ai_cofounder/topics.py`; slice 2's router sets it. Until then every topic is null. |

**What's involved:** `app/db/models/ai_chat.py`, `alembic/versions/0097_ai_conversation_archive.py`, `app/schemas/ai_chat.py`, `app/services/ai_cofounder/service.py`, `app/services/ai_cofounder/topics.py`, `app/api/v1/endpoints/ai.py`, `app/services/search/service.py`.

**Verification:** `tests/api/test_ai_conversations_slice1.py` (status per role, search by title/reply, wildcard escaping, removed messages never match, topic filter and 422, privacy between members, archive/unarchive/search/⌘K/post-409, all-or-nothing bulk, delete, role 403s), `tests/api/test_ai_chat.py` (updated), `tests/test_ai_conversation_archive_migration.py`, alembic upgrade, check, downgrade to 0095 and to base, upgrade again on a scratch DB, live journey `e2e/test_ai_conversations_slice1.py` (stub LLM; captures in `e2e/_captures/ai_conversations/`). Not verified: 403s over live HTTP for non-F/TM roles.

**Operate / roll back:** no env var, no worker change. Downgrade `0097` drops both columns (archived state is lost); revert the PR with it, since the code reads the columns.

**Follow-ups:** slice 2 fills `topic`; FE needs the Archived filter, chips, snippet highlight; generated titles (still first 80 chars) are not part of this slice.
