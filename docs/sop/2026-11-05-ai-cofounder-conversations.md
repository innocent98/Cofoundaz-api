# AI Co-Founder: conversation management (slice 1)

**What shipped:** gap-list rows 12, 13, 14, 10 (`query` + `topic`) and 17 (archive, "Delete forever") from `docs/checklist/ai-cofounder-prd-gaps.md` (FE repo). Branch `feature/ai-cofounder-slice-1`, migration **`0097_ai_conversation_archive`** on `0095_funding_data_room` (the lead re-chains). FE guide `docs/fe-integration-guide-ai-conversations.md`.

**Why:** a blank message made a model call and an empty title; an empty conversation could be created; `/ai/status` leaked token spend and failure times to mentor and accountant; list filters were ignored; and a founder had no way to remove a private conversation.

**How / decisions**

| Topic | Decision |
|---|---|
| Blank input (D-AI-9) | `StringConstraints(strip_whitespace, min_length=1)` on both bodies: 422 before any job. |
| Empty conversation (D-AI-8) | `message` is required on create; the service no longer has a no-message path. |
| Status (D-AI-7) | `/ai/status` uses the same F + TM dependency as the rest of the module. |
| Search | `query` is trimmed, then must be 2 to 100 chars (blank = no query, 422 otherwise); it is an `ILIKE` over the title and an `EXISTS` over ready, non-moderation-removed messages. `%`/`_`/`\` are escaped. Snippet is a window around the first message hit (title if only the title matched). No full-text index: lists are per user and capped at 200, so a scan is fine. |
| Archive (D-AI-4) | `archived_at` timestamp. Default list and ⌘K search hide archived; `archived=true` lists them; `archived=true` with `query` is 422 (not searchable). Posting to an archived chat is `409 CONVERSATION_ARCHIVED`. Bulk archive/unarchive take up to 100 ids, are idempotent, and are all-or-nothing: any id that is not the caller's is a 404, so ids give no existence signal. |
| Delete forever | `DELETE /conversations/{id}`; the FK cascade removes messages. Refused with 409 `REPLY_IN_PROGRESS` while a turn is `generating` (stale ones are failed first). Defence in depth: after the model call the worker re-reads its row `FOR UPDATE`; if the conversation is gone it returns normally so the already-flushed debit commits with the job (before, the write to the gone row raised, the savepoint rolled the debit back and a POST+DELETE loop spent tokens for free; found in review). Reported-message snippets in `content_reports` are separate copies kept by Admin moderation (D-ADM-9) and are not removed. |
| Topic | Column and filter only. Values are fixed in `app/services/ai_cofounder/topics.py`; slice 2's router sets it. Until then every topic is null. |

**What's involved:** `app/db/models/ai_chat.py`, `alembic/versions/0097_ai_conversation_archive.py`, `app/schemas/ai_chat.py`, `app/services/ai_cofounder/service.py`, `app/services/ai_cofounder/topics.py`, `app/api/v1/endpoints/ai.py`, `app/services/search/service.py`.

**Verification:** `tests/worker/test_cofounder_reply_handler.py` (delete during a successful or a failing model call: no raise, tokens billed, no failure event; fails without the fix), `tests/api/test_ai_conversations_slice1.py` (query trimming, delete 409 and stale, status per role, search by title/reply, wildcard escaping, removed messages never match, topic filter and 422, privacy between members, archive/unarchive/search/⌘K/post-409, all-or-nothing bulk, delete, role 403s), `tests/api/test_ai_chat.py` (updated), `tests/test_ai_conversation_archive_migration.py`, alembic upgrade, check, downgrade to 0095 and to base, upgrade again on a scratch DB, live journey `e2e/test_ai_conversations_slice1.py` (stub LLM; captures in `e2e/_captures/ai_conversations/`). Not verified: 403s over live HTTP for non-F/TM roles.

**Operate / roll back:** no env var, no worker change. Downgrade `0097` drops both columns (archived state is lost); revert the PR with it, since the code reads the columns.

**Follow-ups:** slice 2 fills `topic`; FE needs the Archived filter, chips, snippet highlight; generated titles (still first 80 chars) are not part of this slice.
