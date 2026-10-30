# Live e2e gate: billing journey no longer crashes collection

**What shipped:** `e2e/test_billing.py` reads `PAYSTACK_SECRET_KEY` with a fallback, so the file imports without the key.

**Why:** since API #196 every `CD (staging)` run failed at the live e2e gate with `KeyError: 'PAYSTACK_SECRET_KEY'`. The code deployed to staging, but the staging-verified proof was never minted.

**How:** a one-line change. `e2e/conftest.py` already deselects this journey in remote mode because it needs the mailbox, so only the import had to stop failing. Giving the gate a key was rejected: the journey can't run against a deployed API anyway.

**What's involved:** `e2e/test_billing.py:18`.

**Verification:** with `E2E_REMOTE=1` and no key, pytest collects the file and reports `1 deselected`. The gate's next run on `develop` is the real proof, and may surface other failures that the collection error was hiding.

**Roll back:** revert the commit.

**Follow-ups:** none.
