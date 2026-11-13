# Live E2E gate: collection no longer builds `Settings()` under E2E_REMOTE

| | |
|---|---|
| **What shipped** | Three e2e modules skip themselves at module level under `E2E_REMOTE`, before their helper imports. Branch `ci/live-e2e-collection`. |
| **Why** | The `CD (staging)` "Live E2E gate" failed on every develop merge since 2026-11-06 at collection, not in a test. `e2e/test_funding_term_sheets.py`, `test_readiness_deck_review.py` and `test_readiness_narrative.py` import `tests.deck_helpers` / `term_helpers` / `narrative_helpers`, which import app code, which builds `Settings()`. The remote runner has no `SECRET_KEY`, `DATABASE_URL` or superuser vars, so pydantic raised a `ValidationError` and the whole run aborted. |
| **How** | A `pytest.skip(..., allow_module_level=True)` guard on `E2E_REMOTE`, placed above the helper imports, in each file. Chosen over making the helpers settings-free because these journeys use a canned in-process LLM, which cannot be injected into a deployed server: running them remotely would not verify staging anyway. A visible skip is honest about that; the staging gate does not claim these journeys. They still run in the CI/local e2e job (`scripts/e2e_run.sh`). |
| **Other imports checked** | Every module-level `app.` / `tests.` import in `e2e/`: `test_google_sign_in.py` (`app.services.auth.google_stub`) and `test_readiness_mock_qa.py` (`tests.qa_helpers`) collect fine with no app env, so they were left alone. |
| **Verification** | `E2E_REMOTE=1 pytest e2e --collect-only` with an empty environment: 0 errors, 16 collected (120 deselected: the existing mailbox rule plus these skips). The three journeys pass locally against a scratch DB. CI is the authority for the rest. |
| **Roll back** | Revert the three guards. |
| **Follow-up** | A new e2e module that imports `app`/`tests` at module level will break the remote gate the same way; consider a CI step that runs the remote collect-only with an empty env. |
