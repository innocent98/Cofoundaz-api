import json

from app.platform.email import ConsoleEmailSender, EmailMessage, FileEmailSender, get_email_sender


def test_console_sender_records():
    s = ConsoleEmailSender()
    s.send(EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>"))
    assert s.sent[-1].to == "a@b.com"
    assert s.sent[-1].subject == "Hi"


def test_factory_returns_console_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMAIL_BACKEND", "console")
    assert isinstance(get_email_sender(), ConsoleEmailSender)


def test_file_sender_writes_json(tmp_path):
    s = FileEmailSender(mail_dir=str(tmp_path))
    s.send(EmailMessage(to="e2e@x.com", subject="Verify your email", html="<p>tok: abc</p>"))
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["to"] == "e2e@x.com"
    assert data["subject"] == "Verify your email"
    assert data["html"] == "<p>tok: abc</p>"


def test_factory_returns_file_when_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMAIL_BACKEND", "file")
    assert isinstance(get_email_sender(), FileEmailSender)


# --------------------------------------------------------------------------- #
# SMTP backend.
#
# These drive the REAL `emails` library and assert on the MIME message it
# produces; only the final network send is stubbed. Mocking emails.Message
# itself would prove nothing about a version bump, which is the exact thing
# that broke here (0.6 -> 1.1.2 made the mail_from address non-optional).
# --------------------------------------------------------------------------- #


def _capture_sent(monkeypatch):
    """Stub only the network hop, and hand back the built MIME message."""
    import emails

    captured: dict[str, object] = {}

    def fake_send(self, **kwargs):
        captured["from"] = self.as_message()["From"]
        captured["to"] = kwargs.get("to")
        return None

    monkeypatch.setattr(emails.Message, "send", fake_send)
    return captured


def test_smtp_sender_builds_from_header_with_name(monkeypatch):
    from app.core.config import settings
    from app.platform.email import SMTPEmailSender

    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@cofoundaz.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", "Cofoundaz")
    captured = _capture_sent(monkeypatch)

    SMTPEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))

    assert captured["from"] == "Cofoundaz <no-reply@cofoundaz.com>"
    assert captured["to"] == "user@x.com"


def test_smtp_sender_allows_an_unset_from_name(monkeypatch):
    """A missing display name is legal and yields a bare-address From."""
    from app.core.config import settings
    from app.platform.email import SMTPEmailSender

    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@cofoundaz.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", None)
    captured = _capture_sent(monkeypatch)

    SMTPEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))

    assert captured["from"] == "no-reply@cofoundaz.com"


def test_smtp_sender_refuses_to_send_without_a_from_address(monkeypatch):
    """An unset EMAILS_FROM_EMAIL must fail loudly, not send a From-less message.

    emails 1.x builds NO From header at all from (None, None). RFC 5322 requires
    one, so such a message is rejected or quarantined by the receiving MTA - and
    because SMTPEmailSender discards the send result, that failure would be
    completely silent. Verified against emails 1.1.2.
    """
    import pytest

    from app.core.config import settings
    from app.platform.email import SMTPEmailSender

    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", None)
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", "Cofoundaz")

    with pytest.raises(RuntimeError, match="EMAILS_FROM_EMAIL is not set"):
        SMTPEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))


def test_factory_returns_smtp_when_configured(monkeypatch):
    from app.core.config import settings
    from app.platform.email import SMTPEmailSender

    monkeypatch.setattr(settings, "EMAIL_BACKEND", "smtp")
    assert isinstance(get_email_sender(), SMTPEmailSender)


# --------------------------------------------------------------------------- #
# Resend backend.
#
# Drives ResendEmailSender with the network hop (httpx.post) stubbed, asserting
# the request it builds and its fail-loud behaviour. No real Resend calls.
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_factory_returns_resend_when_configured(monkeypatch):
    from app.core.config import settings
    from app.platform.email import ResendEmailSender

    monkeypatch.setattr(settings, "EMAIL_BACKEND", "resend")
    assert isinstance(get_email_sender(), ResendEmailSender)


def test_resend_sender_posts_expected_request(monkeypatch):
    from app.core.config import settings
    from app.platform import email as email_mod
    from app.platform.email import ResendEmailSender

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@cofoundaz.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", "Cofoundaz")

    captured: dict[str, object] = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeResponse(200)

    monkeypatch.setattr(email_mod.httpx, "post", fake_post)
    ResendEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test_key"
    body = captured["json"]
    assert body["from"] == "Cofoundaz <no-reply@cofoundaz.com>"
    assert body["to"] == ["user@x.com"]
    assert body["subject"] == "Hi" and body["html"] == "<p>x</p>"


def test_resend_sender_bare_from_without_name(monkeypatch):
    from app.core.config import settings
    from app.platform import email as email_mod
    from app.platform.email import ResendEmailSender

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@cofoundaz.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        email_mod.httpx,
        "post",
        lambda url, *, headers, json, timeout: captured.update(json=json) or _FakeResponse(200),
    )
    ResendEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))
    assert captured["json"]["from"] == "no-reply@cofoundaz.com"


def test_resend_sender_raises_on_non_2xx(monkeypatch):
    import pytest

    from app.core.config import settings
    from app.platform import email as email_mod
    from app.platform.email import ResendEmailSender

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@cofoundaz.com")
    monkeypatch.setattr(
        email_mod.httpx,
        "post",
        lambda url, *, headers, json, timeout: _FakeResponse(422, '{"message":"bad"}'),
    )
    with pytest.raises(RuntimeError, match="Resend API returned 422"):
        ResendEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))


def test_resend_sender_refuses_without_api_key(monkeypatch):
    import pytest

    from app.core.config import settings
    from app.platform.email import ResendEmailSender

    monkeypatch.setattr(settings, "RESEND_API_KEY", None)
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@cofoundaz.com")
    with pytest.raises(RuntimeError, match="RESEND_API_KEY is not set"):
        ResendEmailSender().send(EmailMessage(to="user@x.com", subject="Hi", html="<p>x</p>"))
