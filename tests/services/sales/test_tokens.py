import uuid

from app.core.config import settings
from app.services.sales.tokens import sign_send, verify_token


def test_round_trip():
    sid = uuid.uuid4()
    assert verify_token(sign_send(sid)) == sid


def test_tampered_or_forged_tokens_are_rejected():
    sid = uuid.uuid4()
    left, right = sign_send(sid).split(".")
    other = sign_send(uuid.uuid4()).split(".")[1]
    for bad in (
        f"{left}.{other}",  # someone else's MAC
        f"{left}.{right[:-2]}AA",  # flipped MAC bytes
        f"{sign_send(uuid.uuid4()).split('.')[0]}.{right}",  # swapped id
        left,
        "",
        ".",
        "a.b.c",
        "x" * 500,
        f"{left}.!!!!",
    ):
        assert verify_token(bad) is None, bad


def test_key_is_domain_separated_and_rotatable(monkeypatch):
    sid = uuid.uuid4()
    monkeypatch.setattr(settings, "SALES_OUTREACH_UNSUBSCRIBE_KEY", "")
    base = sign_send(sid)
    monkeypatch.setattr(settings, "SALES_OUTREACH_UNSUBSCRIBE_KEY", "dedicated-test-key")
    dedicated = sign_send(sid)
    assert base != dedicated
    assert verify_token(dedicated) == sid and verify_token(base) is None
