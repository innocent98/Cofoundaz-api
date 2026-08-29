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
