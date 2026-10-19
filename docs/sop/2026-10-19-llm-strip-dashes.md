# SOP: strip em/en dashes from all LLM output

| | |
|---|---|
| **What shipped** | Every real LLM completion (text and JSON) is cleaned of em/en dashes before any caller sees it. Branch `fix/strip-llm-dashes`. |
| **Why** | Product copy rule: no em/en dashes in rendered copy. Models use them constantly; a mission "Why" line rendered "value—giving". |
| **How** | `strip_dashes()` in `app/platform/llm.py` runs inside `OpenAILLMClient.complete()` and, recursively over every string value, `complete_json()`. Rules: digit ranges `3–5` become `3-5`; a dash opening a line becomes a `- ` bullet; any other dash (spaced or not) becomes `, `. Done at the one seam all LLM output passes through (`llm_budget.metered_*` → `get_llm_client()`), so no caller can forget it. The stub client is untouched (it never emits dashes). |
| **What's involved** | `app/platform/llm.py` (`strip_dashes`, `_strip_dashes_deep`), `tests/platform/test_llm.py` (8 new cases). |
| **Verification** | `pytest tests/platform` 69 passed; full suite 2372 passed at 97.39%; black/isort/ruff/mypy/bandit/pylint clean. |
| **Operate / roll back** | No config or migration. Revert the commit to roll back. Text already stored before this change keeps its dashes (new output only). |
| **Follow-ups** | None. Prompt-level "don't use dashes" instructions are now optional. |
