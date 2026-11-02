"""Move every legacy public expense receipt behind authentication (one-off, idempotent).

The deployed image does not ship `scripts/`, so run the module directly there:

    python -m app.services.finance.receipts_backfill

This wrapper keeps working from a source checkout. Prints `moved=N failed=M`; re-run until
`failed=0`.
"""

from app.services.finance.receipts_backfill import main

if __name__ == "__main__":
    raise SystemExit(main())
