import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/oauth/google",
        "/api/v1/auth/oauth/apple",
        "/api/v1/auth/mfa/sms/setup",
        "/api/v1/auth/mfa/sms/verify",
    ],
)
def test_seams_return_501(client, path):
    r = client.post(path, json={})
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "FEATURE_NOT_ENABLED"
