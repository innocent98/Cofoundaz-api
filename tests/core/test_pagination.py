import uuid
from datetime import UTC, datetime

import pytest

from app.core.pagination import decode_cursor, encode_cursor


def test_cursor_roundtrip():
    ts = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    rid = uuid.uuid4()
    token = encode_cursor(ts, rid)
    assert isinstance(token, str)
    ts2, rid2 = decode_cursor(token)
    assert ts2 == ts and rid2 == rid


def test_bad_cursor_raises():
    with pytest.raises(ValueError):
        decode_cursor("not-base64!!")
