"""One-off backfill: move every legacy (publicly uploaded) receipt behind authentication.

Receipts uploaded before private receipts keep working through the API, but their old public
Cloudinary URL stays live until the asset is moved, which otherwise only happens the first time a
receipt is opened. Run this once per environment after deploying migration 0067
(`python -m scripts.privatise_legacy_receipts`). It is idempotent and safe to re-run: rows already
private are never selected, a Cloudinary asset that was already moved is accepted, and a failure
on one receipt is counted and skipped without stopping the batch.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.expense import Expense
from app.platform.storage import Storage

DEFAULT_BATCH = 100


@dataclass
class BackfillResult:
    moved: int = 0
    failed: int = 0

    @property
    def scanned(self) -> int:
        return self.moved + self.failed


def privatise_legacy_receipts(
    db: Session, storage: Storage, *, batch_size: int = DEFAULT_BATCH
) -> BackfillResult:
    """Call `make_private` for every expense with a receipt that is not yet private.

    Walks the table in id order in batches and commits after each batch, so an interruption
    loses at most one batch of work (and re-running picks up where it stopped).
    """
    result = BackfillResult()
    last_id: uuid.UUID | None = None
    while True:
        q = db.query(Expense).filter(
            Expense.receipt_key.is_not(None), Expense.receipt_private.is_(False)
        )
        if last_id is not None:
            q = q.filter(Expense.id > last_id)
        rows = q.order_by(Expense.id).limit(batch_size).all()
        if not rows:
            return result
        moved = failed = 0
        for exp in rows:
            last_id = exp.id
            key = exp.receipt_key or ""
            try:
                storage.make_private(key)
            except Exception:  # noqa: BLE001  (one bad asset must not stop the run)
                log.exception("receipt backfill failed for expense {}", exp.id)
                failed += 1
                continue
            exp.receipt_private = True
            exp.receipt_url = None
            moved += 1
        db.commit()
        result.moved += moved
        result.failed += failed
        log.info("receipt backfill batch: moved={} failed={}", moved, failed)
