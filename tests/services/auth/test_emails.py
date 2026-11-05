from app.core.config import settings
from app.services.auth.emails import password_reset_email, verification_email


def test_verification_email_has_clickable_link_and_body(monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    msg = verification_email("user@x.com", "tok_raw-123ABC")
    assert msg.to == "user@x.com"
    assert msg.subject == "Verify your email"
    # a real clickable CTA pointing at the FE verify route with the token in the path
    assert 'href="https://app.example.test/verify-email/tok_raw-123ABC"' in msg.html
    assert "Verify email" in msg.html  # CTA label
    assert "24 hours" in msg.html  # expiry note
    assert "ignore this email" in msg.html.lower()
    # no longer the bare "token: <code>...</code>" stub
    assert "token: <code>" not in msg.html


def test_password_reset_email_has_clickable_link_and_body(monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    msg = password_reset_email("user@x.com", "reset-raw-456")
    assert msg.subject == "Reset your password"
    assert 'href="https://app.example.test/reset-password/reset-raw-456"' in msg.html
    assert "Reset password" in msg.html
    assert "1 hour" in msg.html


def test_link_base_falls_back_to_server_host_when_app_base_url_empty(monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "")
    monkeypatch.setattr(settings, "SERVER_HOST", "https://api.example.test/")
    msg = verification_email("user@x.com", "abc")
    # trailing slash trimmed; falls back to SERVER_HOST when APP_BASE_URL is empty
    assert "https://api.example.test/verify-email/abc" in msg.html
    assert "https://api.example.test//verify-email" not in msg.html


def test_invitation_subject_names_the_inviter_and_the_startup():
    from app.services.auth.emails import invitation_email

    msg = invitation_email(
        "mate@x.com", "tok", startup_name="NovaPay", inviter_name="Ada Obi", role="mentor"
    )
    assert msg.subject == "Ada Obi invited you to join NovaPay on Cofoundaz"
    anon = invitation_email("m@x.com", "t", startup_name=None, inviter_name=None, role="mentor")
    assert anon.subject == "A founder invited you to join a startup on Cofoundaz"


def test_invitation_subject_cannot_carry_a_header_injection():
    from app.services.auth.emails import invitation_email

    msg = invitation_email(
        "m@x.com", "t", startup_name="Evil\r\nBcc: x@y.z", inviter_name="Ada\nObi", role="mentor"
    )
    assert "\n" not in msg.subject and "\r" not in msg.subject


def test_account_emails_use_the_brand_blue_not_indigo(monkeypatch):
    from app.services.auth.emails import (
        invitation_email,
        password_changed_email,
        welcome_email,
    )

    msgs = [
        verification_email("u@x.com", "a"),
        password_reset_email("u@x.com", "b"),
        invitation_email("u@x.com", "c", startup_name="S", inviter_name="I", role="mentor"),
        welcome_email("u@x.com", startup_name="S", full_name="Ada"),
        password_changed_email("u@x.com"),
    ]
    for msg in msgs:
        assert "#0F6AFC" in msg.html and "#051736" in msg.html
        assert "#4f46e5" not in msg.html.lower()


def test_welcome_and_password_changed_emails(monkeypatch):
    from app.services.auth.emails import password_changed_email, welcome_email

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    w = welcome_email("u@x.com", startup_name="Novă <Pay>", full_name="Ada Obi")
    assert w.subject == "Welcome to Cofoundaz: let's build Novă <Pay>"
    assert "Novă &lt;Pay&gt;" in w.html and "<Pay>" not in w.html  # escaped in the body
    assert 'href="https://app.example.test/dashboard"' in w.html
    p = password_changed_email("u@x.com")
    assert p.subject == "Your Cofoundaz password was changed"
    assert 'href="https://app.example.test/forgot-password"' in p.html
