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
