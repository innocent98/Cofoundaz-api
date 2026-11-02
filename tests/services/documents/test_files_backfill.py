import pytest

import app.services.documents.files_backfill as mod
from app.core.config import settings
from app.db.models.document import DocumentFile
from app.db.models.enums import ScanStatus
from app.platform.storage import LocalStorage
from app.services.documents.files_backfill import backfill_files, main
from tests.factories import create_startup, create_user


def _legacy(db, s, u, tmp_path, name, body, *, ctype="application/pdf", private=False, status=None):
    key = f"documents/{s.id}/{name}"
    path = tmp_path / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    f = DocumentFile(
        startup_id=s.id,
        uploaded_by_id=u.id,
        filename=name,
        content_type=ctype,
        size_bytes=len(body),
        storage_key=key,
        url=None if private else "https://public.example/" + name,
        is_private=private,
        scan_status=status or ScanStatus.pending,
    )
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s, tmp_path


def test_good_legacy_file_is_made_private_and_scanned_clean(db, world):
    u, s, p = world
    f = _legacy(db, s, u, p, "ok.pdf", b"%PDF-1.4 x")
    res = backfill_files(db, LocalStorage(str(p)))
    db.refresh(f)
    assert (f.is_private, f.url, f.scan_status) == (True, None, ScanStatus.clean)
    assert (res.moved, res.scanned, res.blocked, res.failed) == (1, 1, 0, 0)


def test_exe_posing_as_pdf_is_blocked(db, world):
    u, s, p = world
    f = _legacy(db, s, u, p, "fake.pdf", b"MZ")
    res = backfill_files(db, LocalStorage(str(p)))
    db.refresh(f)
    assert f.scan_status == ScanStatus.blocked and res.blocked == 1


def test_trusted_clean_files_are_only_privatised(db, world):
    u, s, p = world
    f = _legacy(db, s, u, p, "signed.pdf", b"MZ", status=ScanStatus.clean)
    backfill_files(db, LocalStorage(str(p)))
    db.refresh(f)
    assert f.is_private and f.scan_status == ScanStatus.clean  # a request depends on it: untouched


def test_unreadable_bytes_stay_pending(db, world):
    u, s, p = world
    f = _legacy(db, s, u, p, "gone.pdf", b"%PDF-1")
    (p / f.storage_key).unlink()
    backfill_files(db, LocalStorage(str(p)))
    db.refresh(f)
    assert f.scan_status == ScanStatus.pending and f.is_private


def test_one_failure_does_not_stop_the_batch_and_rerun_is_a_noop(db, world):
    u, s, p = world
    bad = _legacy(db, s, u, p, "a.pdf", b"%PDF-1")
    good = _legacy(db, s, u, p, "b.pdf", b"%PDF-2")

    class Flaky(LocalStorage):
        def make_private(self, key):
            if key == bad.storage_key:
                raise OSError("provider down")

    res = backfill_files(db, Flaky(str(p)), batch_size=1)
    db.refresh(good)
    assert res.failed == 1 and good.scan_status == ScanStatus.clean
    again = backfill_files(db, LocalStorage(str(p)))
    assert again.failed == 0
    assert backfill_files(db, LocalStorage(str(p))).moved == 0


def test_main_exit_code(db, world, monkeypatch, capsys):
    class S:
        def close(self):
            pass

    monkeypatch.setattr(mod, "SessionLocal", lambda: S())
    monkeypatch.setattr(mod, "backfill_files", lambda d, st: mod.BackfillResult(failed=1))
    assert main() == 1 and "failed=1" in capsys.readouterr().out
    monkeypatch.setattr(mod, "backfill_files", lambda d, st: mod.BackfillResult())
    assert main() == 0


def test_a_row_that_fails_midway_rolls_back_alone(db, world, monkeypatch):
    """The failing row's partial writes (is_private flipped before the scan blew up) are undone by
    its savepoint; the rest of the same batch still commits."""
    u, s, p = world
    bad = _legacy(db, s, u, p, "a.pdf", b"%PDF-1")
    good = _legacy(db, s, u, p, "b.pdf", b"%PDF-2")
    real = mod.scan_file

    def boom(d, f, content):
        if f.id == bad.id:
            raise RuntimeError("scanner down")
        real(d, f, content)

    monkeypatch.setattr(mod, "scan_file", boom)
    res = backfill_files(db, LocalStorage(str(p)))  # one batch holds both rows
    db.expire_all()
    db.refresh(bad)
    db.refresh(good)
    assert res.failed == 1
    assert bad.is_private is False and bad.url and bad.scan_status == ScanStatus.pending
    assert good.is_private is True and good.scan_status == ScanStatus.clean


def test_remote_read_stops_at_the_size_cap(monkeypatch):
    class Remote:
        def private_url(self, key, ttl):
            return "https://cdn.example/x"

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            self.pulled = 0
            for _ in range(100):
                self.pulled += 1
                yield b"x" * (mod._MAX_BYTES // 4)

    resp = Resp()
    monkeypatch.setattr(mod.httpx, "stream", lambda *a, **k: resp)
    assert mod._read(Remote(), "k") is None
    assert resp.pulled == 5  # 4 chunks fit, the 5th crosses the cap and ends the read

    class Small(Resp):
        def iter_bytes(self):
            yield b"%PDF-1"

    monkeypatch.setattr(mod.httpx, "stream", lambda *a, **k: Small())
    assert mod._read(Remote(), "k") == b"%PDF-1"
