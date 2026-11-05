import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.data_room import DataRoomFolder, DataRoomItem
from app.db.models.document import DocumentFile
from app.db.models.enums import ScanStatus
from tests.factories import create_startup, create_user


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], capture_output=True, text=True, check=False
    )


def test_migration_chain_single_head_includes_0095():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    assert "0095_funding_data_room" in _alembic("history").stdout


@pytest.fixture()
def ws(db):
    s = create_startup(db, owner=create_user(db))
    db.flush()
    return s


def _folder(db, ws, name="Legal"):
    f = DataRoomFolder(startup_id=ws.id, name=name, position=0, is_standard=False)
    db.add(f)
    db.flush()
    return f


def _file(db, ws):
    f = DocumentFile(
        startup_id=ws.id,
        filename="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key=f"k/{datetime.now(UTC).timestamp()}",
        scan_status=ScanStatus.clean,
    )
    db.add(f)
    db.flush()
    return f


def test_an_item_points_at_exactly_one_target(db, ws):
    folder = _folder(db, ws)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(DataRoomItem(startup_id=ws.id, folder_id=folder.id, position=0))
            db.flush()


def test_the_same_file_cannot_sit_twice_in_a_folder_but_can_in_two(db, ws):
    a, b = _folder(db, ws, "A"), _folder(db, ws, "B")
    f = _file(db, ws)
    db.add(DataRoomItem(startup_id=ws.id, folder_id=a.id, file_id=f.id, position=0))
    db.add(DataRoomItem(startup_id=ws.id, folder_id=b.id, file_id=f.id, position=0))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(DataRoomItem(startup_id=ws.id, folder_id=a.id, file_id=f.id, position=1))
            db.flush()


def test_deleting_a_documents_file_removes_its_reference(db, ws):
    folder = _folder(db, ws)
    f = _file(db, ws)
    item = DataRoomItem(startup_id=ws.id, folder_id=folder.id, file_id=f.id, position=0)
    db.add(item)
    db.flush()
    item_id = item.id
    db.delete(f)
    db.flush()
    db.expunge_all()
    assert db.query(DataRoomItem).filter_by(id=item_id).count() == 0
