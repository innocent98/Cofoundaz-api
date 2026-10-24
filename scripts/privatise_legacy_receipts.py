"""Move every legacy public expense receipt behind authentication (one-off, idempotent).

    python -m scripts.privatise_legacy_receipts

Run once per environment after deploying migration 0067. Uses the configured storage backend, so
it needs the Cloudinary credentials of that environment when STORAGE_BACKEND=cloudinary. Prints
`moved=N failed=M`; re-run until `failed=0`.
"""

from app.db.session import SessionLocal
from app.platform.storage import get_storage
from app.services.finance.receipts_backfill import privatise_legacy_receipts


def main() -> int:
    db = SessionLocal()
    try:
        result = privatise_legacy_receipts(db, get_storage())
    finally:
        db.close()
    print(f"moved={result.moved} failed={result.failed}")  # noqa: T201
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
