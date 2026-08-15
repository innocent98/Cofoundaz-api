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
