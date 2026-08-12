from app.core.config import settings


def test_auth_defaults():
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 30
    assert settings.LOGIN_MAX_FAILS == 5
    assert settings.LOGIN_LOCKOUT_MINUTES == 15
    assert settings.REFRESH_COOKIE_SAMESITE == "lax"
    assert settings.RATE_LIMIT_PER_MINUTE == 120
