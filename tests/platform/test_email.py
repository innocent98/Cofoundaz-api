from app.platform.email import ConsoleEmailSender, EmailMessage, get_email_sender


def test_console_sender_records():
    s = ConsoleEmailSender()
    s.send(EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>"))
    assert s.sent[-1].to == "a@b.com"
    assert s.sent[-1].subject == "Hi"


def test_factory_returns_console_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMAIL_BACKEND", "console")
    assert isinstance(get_email_sender(), ConsoleEmailSender)
