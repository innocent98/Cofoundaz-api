import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings


def test_auth_defaults():
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 30
    assert settings.LOGIN_MAX_FAILS == 5
    assert settings.LOGIN_LOCKOUT_MINUTES == 15
    assert settings.REFRESH_COOKIE_SAMESITE == "lax"
    assert settings.RATE_LIMIT_PER_MINUTE == 120


def test_refresh_cookie_samesite_lowercases_valid_value():
    s = Settings(REFRESH_COOKIE_SAMESITE="Strict")  # type: ignore[call-arg]
    assert s.REFRESH_COOKIE_SAMESITE == "strict"


@pytest.mark.parametrize("bad", ["Bogus", "", "cross-site", "None-ish"])
def test_refresh_cookie_samesite_rejects_invalid_value(bad):
    with pytest.raises(ValidationError):
        Settings(REFRESH_COOKIE_SAMESITE=bad)  # type: ignore[call-arg]


def test_outreach_hosts_are_normalised_and_validated():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    base = {"DATABASE_URL": "postgresql://u:p@h/d", "SECRET_KEY": "k"}
    s = Settings(
        **base,
        SALES_OUTREACH_SMTP_HOST=" smtps://mail.example.com/ ",
        SALES_OUTREACH_IMAP_HOST="mail.example.com/",
    )
    assert (
        s.SALES_OUTREACH_SMTP_HOST == "mail.example.com"
        and s.SALES_OUTREACH_IMAP_HOST == "mail.example.com"
    )
    with pytest.raises(ValidationError):
        Settings(**base, SALES_OUTREACH_SMTP_HOST="mail example.com:465/x")
    with pytest.raises(ValidationError):
        Settings(**base, SALES_OUTREACH_SMTP_SECURITY="tls")
