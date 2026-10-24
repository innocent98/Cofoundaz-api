from app.db.models.document import DocumentFile
from app.db.models.enums import ScanStatus
from app.platform.events import event_bus
from app.services.documents import scanner
from app.services.documents.scanner import NoopScanner, apply_verdict, get_scanner, scan_file
from tests.factories import create_membership, create_startup, create_user


def _file(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    f = DocumentFile(
        startup_id=s.id,
        uploaded_by_id=u.id,
        filename="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key="k",
        url="u",
    )
    db.add(f)
    db.flush()
    db.refresh(f)
    return f


def test_new_rows_default_to_pending(db):
    assert _file(db).scan_status == ScanStatus.pending


def test_noop_scanner_marks_clean_and_stamps_time(db):
    f = _file(db)
    assert isinstance(get_scanner(), NoopScanner)
    scan_file(db, f, b"%PDF-1.4")
    assert f.scan_status == ScanStatus.clean and f.scanned_at is not None


def test_blocked_verdict_is_recorded_and_announced(db, monkeypatch):
    f = _file(db)
    seen = []
    monkeypatch.setattr(event_bus, "publish", lambda _db, e, p: seen.append((e, p)))
    apply_verdict(db, f, ScanStatus.blocked)
    assert f.scan_status == ScanStatus.blocked and f.scanned_at is not None
    assert seen == [
        ("document.file.blocked", {"startup_id": str(f.startup_id), "file_id": str(f.id)})
    ]


def test_pending_verdict_leaves_the_file_unscanned(db, monkeypatch):
    """A scanner that answers later returns pending; nothing is stamped until the verdict."""

    class Later:
        def scan(self, content, filename):
            return ScanStatus.pending

    monkeypatch.setattr(scanner, "get_scanner", lambda: Later())
    f = _file(db)
    scan_file(db, f, b"x")
    assert f.scan_status == ScanStatus.pending and f.scanned_at is None
