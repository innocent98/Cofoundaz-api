"""One-off backfill for Documents files uploaded before they were private and scanned.

Run once per environment after deploying migration 0072
(`python -m app.services.documents.files_backfill`, which works inside the deployed image).
For every file that is not yet `is_private`, or is still `pending`, it:

1. moves the stored object behind authentication (`make_private`), clearing the legacy public
   `url`, so a URL that leaked earlier stops working;
2. re-reads the stored bytes and re-runs the magic-byte check against the recorded type, marking
   a mismatch (a pre-check upload such as `MZ` sent as `application/pdf`) `blocked`, otherwise
   handing the file to the scanner hook, which decides `clean` / `pending`.

Idempotent and safe to re-run: a failure on one file is counted and skipped without stopping the
batch, and a file whose bytes cannot be read stays `pending` (never promoted unchecked).
"""

import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.document import DocumentFile
from app.db.models.enums import ScanStatus
from app.db.session import SessionLocal
from app.platform.storage import LocalStorage, Storage, get_storage
from app.services.documents.scanner import apply_verdict, scan_file
from app.services.documents.sniff import sniff_content_type

DEFAULT_BATCH = 100
_FETCH_TIMEOUT = 30.0
_MAX_BYTES = 16 * 1024 * 1024


@dataclass
class BackfillResult:
    moved: int = 0
    scanned: int = 0
    blocked: int = 0
    failed: int = 0


def _read(storage: Storage, key: str) -> bytes | None:
    """The stored bytes of a private object, or None when they cannot be read."""
    if isinstance(storage, LocalStorage):
        path = storage.open_private(key)
        return path.read_bytes() if path else None
    url = storage.private_url(key, 120)
    if url is None:
        return None
    buf = bytearray()
    with httpx.stream("GET", url, timeout=_FETCH_TIMEOUT) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            buf += chunk
            if len(buf) > _MAX_BYTES:
                return None  # never read past the cap; the file stays pending
    return bytes(buf)


def _process(db: Session, storage: Storage, f: DocumentFile, result: BackfillResult) -> None:
    if not f.is_private:
        storage.make_private(f.storage_key)
        f.is_private = True
        f.url = None
        result.moved += 1
    if f.scan_status != ScanStatus.pending:
        return
    content = _read(storage, f.storage_key)
    if content is None:
        return  # stays pending: never promoted without looking at the bytes
    if sniff_content_type(content, f.content_type) is None:
        apply_verdict(db, f, ScanStatus.blocked)
        result.blocked += 1
    else:
        scan_file(db, f, content)
        result.scanned += 1


def backfill_files(
    db: Session, storage: Storage, *, batch_size: int = DEFAULT_BATCH
) -> BackfillResult:
    result = BackfillResult()
    last_id: uuid.UUID | None = None
    while True:
        q = db.query(DocumentFile).filter(
            or_(DocumentFile.is_private.is_(False), DocumentFile.scan_status == ScanStatus.pending)
        )
        if last_id is not None:
            q = q.filter(DocumentFile.id > last_id)
        rows = q.order_by(DocumentFile.id).limit(batch_size).all()
        if not rows:
            return result
        for f in rows:
            last_id = f.id
            try:
                with db.begin_nested():  # a failed row rolls back alone, not the whole batch
                    _process(db, storage, f, result)
            except Exception:  # noqa: BLE001  (one bad asset must not stop the run)
                log.exception("document file backfill failed for {}", f.id)
                result.failed += 1
        db.commit()
        log.info("document file backfill batch: {}", result)


def main() -> int:
    """Run against the configured database and storage; exit 1 if any file failed."""
    db = SessionLocal()
    try:
        result = backfill_files(db, get_storage())
    finally:
        db.close()
    print(  # noqa: T201
        f"moved={result.moved} scanned={result.scanned} blocked={result.blocked} "
        f"failed={result.failed}"
    )
    return 1 if result.failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
