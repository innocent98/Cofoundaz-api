import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import AppError, NotFound
from app.core.redis import get_redis
from app.db.models.activity import ActivityLog
from app.db.models.enums import LeadSource, LeadStatus, MembershipRole
from app.db.models.sales import SalesContact, SalesLead
from app.schemas.sales import ImportMapping
from app.services.sales import csv_import as svc
from tests.factories import create_membership, create_startup, create_user

MAP = ImportMapping(name="Name", company="Company", email="Email")


@pytest.fixture
def redis():
    r = get_redis()
    yield r
    for k in r.scan_iter("sales:csv:*"):
        r.delete(k)


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s


def _csv(*lines: str, nl: str = "\n") -> bytes:
    return nl.join(lines).encode()


def test_preview_basic_bom_crlf_quoted(redis, db):
    u, s = _ws(db)
    raw = b"\xef\xbb\xbf" + _csv(
        "Full Name,Organisation,E-mail,Source",
        '"Doe, Jane",Acme,jane@acme.co,web',
        "Bob,,bob@acme.co,x",
        nl="\r\n",
    )
    out = svc.preview_csv(redis, s.id, u.id, raw)
    assert out.headers == ["Full Name", "Organisation", "E-mail", "Source"]
    assert out.row_count == 2
    assert out.sample_rows[0][0] == "Doe, Jane"
    assert out.suggested_mapping == {
        "name": "Full Name",
        "company": "Organisation",
        "email": "E-mail",
        "source": "Source",
    }
    stored = json.loads(redis.get(f"sales:csv:{out.upload_id}"))
    assert stored["startup_id"] == str(s.id)
    assert 0 < redis.ttl(f"sales:csv:{out.upload_id}") <= 1800


def test_preview_sample_capped_at_five(redis, db):
    u, s = _ws(db)
    raw = _csv("Name", *[f"n{i}" for i in range(9)])
    out = svc.preview_csv(redis, s.id, u.id, raw)
    assert len(out.sample_rows) == 5 and out.row_count == 9
    assert out.suggested_mapping["email"] is None


def test_preview_limits(redis, db):
    u, s = _ws(db)
    with pytest.raises(AppError) as ei:
        svc.preview_csv(redis, s.id, u.id, b"a" * (2 * 1024 * 1024 + 1))
    assert ei.value.http_status == 413 and ei.value.code == "PAYLOAD_TOO_LARGE"
    ok = _csv("Name", *["x"] * 5000)
    assert svc.preview_csv(redis, s.id, u.id, ok).row_count == 5000
    with pytest.raises(AppError) as ei:
        svc.preview_csv(redis, s.id, u.id, _csv("Name", *["x"] * 5001))
    assert ei.value.http_status == 422


@pytest.mark.parametrize("raw", [b"", b"\n\n", b"\xff\xfe\x00bad", b'Name\n"a\x00b"'])
def test_preview_rejects_unreadable(redis, db, raw):
    u, s = _ws(db)
    with pytest.raises(AppError) as ei:
        svc.preview_csv(redis, s.id, u.id, raw)
    assert ei.value.http_status == 422


def test_commit_happy_dedupe_invalid(redis, db):
    u, s = _ws(db)
    db.add(SalesLead(startup_id=s.id, name="Old", email="Dup@Acme.co"))
    db.add(SalesContact(startup_id=s.id, first_name="C", email="contact@acme.co"))
    db.flush()
    raw = _csv(
        "Name,Company,Email",
        "Jane,Acme,jane@acme.co",
        "Again,Acme,JANE@acme.co",
        "Old,,dup@acme.co",
        "Con,,contact@acme.co",
        ",,nobody@acme.co",
        "Bad,,not-an-email",
        "NoEmail,,",
        "NoEmail2,,",
        ",Co Only,",
    )
    up = svc.preview_csv(redis, s.id, u.id, raw)
    res = svc.commit_csv(db, redis, s.id, u.id, up.upload_id, MAP)
    assert res.added == 4  # Jane, NoEmail, NoEmail2, Co Only
    assert res.duplicates_skipped == 3
    assert res.invalid_total == 2
    assert res.invalid_rows == [
        {"row": 5, "error": "A name or company is required."},
        {"row": 6, "error": "Email is not valid."},
    ]
    rows = list(db.scalars(select(SalesLead).where(SalesLead.name == "Jane")))
    assert rows[0].source == LeadSource.csv and rows[0].status == LeadStatus.new
    act = db.scalar(
        select(ActivityLog).where(
            ActivityLog.startup_id == s.id, ActivityLog.action == "sales.lead.imported"
        )
    )
    assert act.entity_id is None and act.meta["added"] == 4
    assert redis.get(f"sales:csv:{up.upload_id}") is None


def test_error_messages_never_echo_cell_text(redis, db):
    u, s = _ws(db)
    secret = "=cmd|calc!A1"
    up = svc.preview_csv(redis, s.id, u.id, _csv("Name,Email", f"x,{secret}"))
    res = svc.commit_csv(db, redis, s.id, u.id, up.upload_id, MAP_NAME_EMAIL)
    assert secret not in json.dumps(res.model_dump())


def test_invalid_rows_capped_at_50(redis, db):
    u, s = _ws(db)
    up = svc.preview_csv(redis, s.id, u.id, _csv("Name,Email", *["a,bad"] * 60))
    res = svc.commit_csv(db, redis, s.id, u.id, up.upload_id, MAP_NAME_EMAIL)
    assert res.invalid_total == 60 and len(res.invalid_rows) == 50 and res.added == 0


def test_second_commit_and_unknown_upload_404(redis, db):
    u, s = _ws(db)
    up = svc.preview_csv(redis, s.id, u.id, _csv("Name", "a"))
    svc.commit_csv(db, redis, s.id, u.id, up.upload_id, ImportMapping(name="Name"))
    with pytest.raises(NotFound):
        svc.commit_csv(db, redis, s.id, u.id, up.upload_id, ImportMapping(name="Name"))
    with pytest.raises(NotFound):
        svc.commit_csv(db, redis, s.id, u.id, uuid.uuid4().hex, ImportMapping(name="Name"))
    with pytest.raises(NotFound):
        svc.commit_csv(db, redis, s.id, u.id, "../weird", ImportMapping(name="Name"))


def test_foreign_workspace_upload_404_and_not_consumed(redis, db):
    u, s = _ws(db)
    u2, s2 = _ws(db)
    up = svc.preview_csv(redis, s.id, u.id, _csv("Name", "a"))
    with pytest.raises(NotFound):
        svc.commit_csv(db, redis, s2.id, u2.id, up.upload_id, ImportMapping(name="Name"))
    assert redis.get(f"sales:csv:{up.upload_id}") is not None


def test_mapping_validation(redis, db):
    u, s = _ws(db)
    up = svc.preview_csv(redis, s.id, u.id, _csv("Name,Email", "a,a@b.co"))
    with pytest.raises(AppError) as ei:
        svc.commit_csv(db, redis, s.id, u.id, up.upload_id, ImportMapping(email="Email"))
    assert ei.value.http_status == 422
    with pytest.raises(AppError) as ei:
        svc.commit_csv(db, redis, s.id, u.id, up.upload_id, ImportMapping(name="Nope"))
    assert ei.value.http_status == 422
    # a failed mapping must not consume the upload
    res = svc.commit_csv(db, redis, s.id, u.id, up.upload_id, MAP_NAME_EMAIL)
    assert res.added == 1


MAP_NAME_EMAIL = ImportMapping(name="Name", email="Email")


def test_over_long_value_is_invalid(redis, db):
    u, s = _ws(db)
    up = svc.preview_csv(redis, s.id, u.id, _csv("Name", "x" * 201))
    res = svc.commit_csv(db, redis, s.id, u.id, up.upload_id, ImportMapping(name="Name"))
    assert res.added == 0 and res.invalid_total == 1
