from app.core.security import create_access_token
from app.main import _rate_limit_key


class _Req:
    def __init__(self, auth=None):
        self.headers = {"Authorization": auth} if auth else {}
        self.client = type("C", (), {"host": "9.9.9.9"})()


def test_key_uses_user_sub_when_authenticated():
    token = create_access_token("11111111-1111-1111-1111-111111111111")
    assert _rate_limit_key(_Req(f"Bearer {token}")) == "user:11111111-1111-1111-1111-111111111111"


def test_key_falls_back_to_ip():
    assert _rate_limit_key(_Req()) == "9.9.9.9"
