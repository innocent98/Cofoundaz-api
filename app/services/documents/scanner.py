"""Malware-scan hook for uploaded Documents files (gap-list row 5).

Every upload is stored `pending` and handed to the scanner; only `clean` files may be sent for
signature (and shared, once files can be). `blocked` files stay listed so the founder sees why and
can delete them, but nothing downstream will serve them to anyone.

THERE IS NO REAL SCANNER YET. `NoopScanner` marks every file `clean` (after the magic-byte
check in `sniff.py` has already passed). It exists so the state machine, the gates and the
tests are real; wiring ClamAV or a vendor API means writing one class with a `scan` method and
returning it from `get_scanner`. A scanner that cannot answer synchronously may leave the
file `pending` and finish later from a worker via `apply_verdict`; the gates already hold the
file back until then.
"""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.document import DocumentFile
from app.db.models.enums import ScanStatus
from app.platform.events import event_bus


class Scanner(Protocol):
    def scan(self, content: bytes, filename: str) -> ScanStatus:
        """`clean`, `blocked`, or `pending` (the verdict arrives later via `apply_verdict`)."""
        raise NotImplementedError


class NoopScanner:
    """Placeholder: reports every file clean. Replace before accepting untrusted public uploads."""

    def scan(self, content: bytes, filename: str) -> ScanStatus:
        return ScanStatus.clean


def get_scanner() -> Scanner:
    return NoopScanner()


def warn_if_noop_scanner(environment: str) -> None:
    """Shout (WARNING) when the placeholder scanner is live outside dev/test: every upload is then
    waved through as `clean` after the magic-byte check alone."""
    if isinstance(get_scanner(), NoopScanner) and environment not in ("development", "test"):
        log.warning(
            "Documents scanner is the no-op placeholder in environment={}: uploaded files are "
            "marked clean without a malware scan. Wire a real scanner in get_scanner().",
            environment,
        )


def apply_verdict(db: Session, file: DocumentFile, verdict: ScanStatus) -> None:
    file.scan_status = verdict
    if verdict != ScanStatus.pending:
        file.scanned_at = datetime.now(UTC)
    db.flush()
    if verdict == ScanStatus.blocked:
        event_bus.publish(
            db,
            "document.file.blocked",
            {"startup_id": str(file.startup_id), "file_id": str(file.id)},
        )


def scan_file(db: Session, file: DocumentFile, content: bytes) -> None:
    apply_verdict(db, file, get_scanner().scan(content, file.filename))
