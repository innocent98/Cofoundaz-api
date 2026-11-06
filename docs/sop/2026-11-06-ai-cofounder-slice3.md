# AI Co-Founder: actions, feedback, regenerate, suggestions, bench (slice 3)

**What shipped:** MVP slice 3 (gap rows 3, 4, 15, 18, 19; D-AI-1, D-AI-3a, D-AI-6): confirm-first actions with Undo, thumbs feedback, regenerate/retry, aggregated Suggested actions, `GET /ai/agents`, and pinning a conversation to an agent. Branch `feature/ai-cofounder-slice-3`, migration **`0100_ai_slice3`** on `0096_auth_onboarding_gaps` (the lead re-chains if Funding's 0099 lands first). FE guide `docs/fe-integration-guide-ai-cofounder.md` §6.

**Why:** the chat could advise but not act, replies could not be rated or retried, Suggested actions and the bench had no backend.

**How / decisions**

| Topic | Decision |
|---|---|
| Proposals | Same single model call: the reply may end with an `<actions>[...]</actions>` block, parsed after the call (never raises), validated per kind, size-capped, at most 3, kinds allow-listed per agent. Rejected: a second "extract actions" call (more spend). The model never sees ids: milestones are `M1..M5` keys mapped server-side. |
| Execution | `ai_actions` rows (`proposed -> executed -> undone`, or `dismissed`). Confirm re-validates at run time, in a savepoint, with the module's write role; events `ai.action.executed`. Undo reverses only what the action made and refuses (409) if the task snapshot or the milestone values changed, so later edits are never lost. Documents are trashed, not deleted. `update_roadmap` never sets `done` (completing a milestone has side effects owned by the Roadmap page). |
| Regenerate | Reuses the turn, bumps `generation`; jobs carry the generation and both the run and the final conditional UPDATEs check it, so a job from before a Cancel->Regenerate can never fill or fail the new turn (found in review). Action rows are locked before the executed-check, so a confirm in flight is seen. Metered like any reply (pre-flight, debit); refused after an executed action, on archived or moderation-removed messages. |
| Suggestions | `ai_suggestions`, per member, unique `(user, source, ref)` with `ON CONFLICT DO NOTHING`; refreshed on `GET` from briefing, health and learning outputs (no model). Links come from a fixed allow-list. Event `ai.suggestion.created`; one notification per batch (`notify` on the first), category `ai_insights`, digest-eligible. Honors the proactive toggle. |
| Bench / pinning | Registry plus 7 coming-soon entries (copy from the FE bench list). `pinned_agent_key` on the conversation; route method `pinned`. |
| Feedback | `feedback`/`feedback_note` columns, private to the owner, event `ai.cofounder.feedback`. |

**What's involved:** `alembic/versions/0100_ai_slice3.py`; `app/db/models/ai_chat.py` (`AiAction`, `AiSuggestion`, columns); `app/services/ai_cofounder/{actions,suggestions,bench,orchestrator,service,context,agents,prompts}.py`; `app/api/v1/endpoints/ai.py`; `app/services/notifications/{registry,categories,audience}.py`.

**Verification:** `tests/api/test_ai_slice3.py`, `tests/services/ai_cofounder/test_actions_parse.py`, notification audience/read-filter tests updated, live journey `e2e/test_ai_cofounder_slice3.py` (model scripted; captures in `e2e/_captures/ai_slice3/`), alembic upgrade/check/downgrade/upgrade. An Opus review ran over the money and permission paths; its high (generation) and both mediums (confirm vs regenerate, undo snapshot) and the lows (moderation, `done`) are fixed. Not verified: a real model proposing actions; the concurrency fixes under two real connections.

**Operate / roll back:** no env var; deploy API and worker together. Revert the PR and downgrade `0100` (drops actions, suggestions and the new columns; executed changes in Roadmap/Documents stay).

**Follow-ups:** a failed action's `failed` status is not persisted (the request rolls back); tune the actions prompt with a real model; suggestion links are module-level (no per-item deep links); `GET /ai/suggestions` writes on read (a scheduled refresh could replace it); memory deferred (D-AI-5).
