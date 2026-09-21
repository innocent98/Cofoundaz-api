from datetime import timedelta

import pytest

from app.core.errors import AppError, NotFound
from app.services.notifications.service import (
    create_notifications,
    list_notifications,
    mark_all_read,
    mark_read,
    serialize_notification,
    unread_count,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    return u, s


def test_create_one_per_user_and_unread_count(db):
    u, s = _ctx(db)
    u2 = create_user(db)
    rows = create_notifications(
        db,
        user_ids=[u.id, u2.id],
        startup_id=s.id,
        type="document.shared",
        title="Shared",
        body="",
        data={"document_id": "d1"},
    )
    assert len(rows) == 2
    assert unread_count(db, user_id=u.id, startup_id=s.id) == 1


def test_list_scoped_and_unread_filter(db):
    u, s = _ctx(db)
    # Postgres `now()` (the model's created_at server_default) returns the transaction's
    # start time, constant for every statement in this test's savepoint-wrapped
    # transaction -- so both rows would otherwise land on the same created_at and the
    # newest-first ordering would depend on a random UUID tiebreaker. Force a real gap.
    a = create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="a", title="A", body="", data={}
    )[0]
    a.created_at = a.created_at - timedelta(seconds=1)
    db.flush()
    create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="b", title="B", body="", data={}
    )
    items, _cur = list_notifications(
        db, user_id=u.id, startup_id=s.id, unread=False, limit=20, cursor=None
    )
    assert [i.title for i in items] == ["B", "A"]  # newest first
    items_u, _c = list_notifications(
        db, user_id=u.id, startup_id=s.id, unread=True, limit=20, cursor=None
    )
    assert len(items_u) == 2


def test_keyset_pagination(db):
    u, s = _ctx(db)
    for i in range(3):
        create_notifications(
            db, user_ids=[u.id], startup_id=s.id, type="t", title=f"N{i}", body="", data={}
        )
    page1, cur = list_notifications(
        db, user_id=u.id, startup_id=s.id, unread=False, limit=2, cursor=None
    )
    assert len(page1) == 2 and cur is not None
    page2, cur2 = list_notifications(
        db, user_id=u.id, startup_id=s.id, unread=False, limit=2, cursor=cur
    )
    assert len(page2) == 1 and cur2 is None


def test_mark_read_and_all(db):
    u, s = _ctx(db)
    rows = create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="t", title="N", body="", data={}
    )
    mark_read(db, user_id=u.id, startup_id=s.id, notification_id=rows[0].id)
    assert unread_count(db, user_id=u.id, startup_id=s.id) == 0
    # another user's row is not markable
    other = create_user(db)
    with pytest.raises(NotFound):
        mark_read(db, user_id=other.id, startup_id=s.id, notification_id=rows[0].id)
    create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="t2", title="N2", body="", data={}
    )
    assert mark_all_read(db, user_id=u.id, startup_id=s.id) == 1


def test_bad_cursor_422(db):
    u, s = _ctx(db)
    with pytest.raises(AppError) as e:
        list_notifications(
            db, user_id=u.id, startup_id=s.id, unread=False, limit=20, cursor="!!bad!!"
        )
    assert e.value.http_status == 422


def test_serialize_shape(db):
    u, s = _ctx(db)
    n = create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="t", title="N", body="b", data={"k": 1}
    )[0]
    out = serialize_notification(n)
    assert (
        out["type"] == "t"
        and out["title"] == "N"
        and out["read"] is False
        and out["data"] == {"k": 1}
    )
