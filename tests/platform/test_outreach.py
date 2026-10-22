from typing import Any

import pytest

from app.core.config import settings
from app.platform import outreach
from app.platform.outreach import (
    OutreachMessage,
    OutreachNotConfiguredError,
    OutreachRejectedError,
    OutreachUnavailableError,
    SmtpOutreachSender,
    build_mime,
    get_outreach_sender,
    imap_configured,
    outreach_configured,
)
from tests.outreach_fakes import FakeSmtpServer
from tests.sales_outreach_helpers import configure


def _msg(**kw) -> OutreachMessage:
    base: dict[str, Any] = {
        "to": "buyer@prospect.test",
        "from_email": "outreach@mail.example.test",
        "from_name": "Fiona at Acme",
        "reply_to": "fiona@acme.test",
        "subject": "Hello",
        "text": "plain body",
        "html": "<p>html body</p>",
        "message_id": "<abc@mail.example.test>",
        "headers": {"X-Cofoundaz-Step": "abc", "List-Unsubscribe": "<https://api.test/u/t>"},
    }
    base.update(kw)
    return OutreachMessage(**base)


def test_mime_has_the_required_headers_and_both_bodies():
    m = build_mime(_msg())
    assert m["Reply-To"] == "fiona@acme.test" and m["Message-ID"] == "<abc@mail.example.test>"
    assert m["List-Unsubscribe"] == "<https://api.test/u/t>" and m["X-Cofoundaz-Step"] == "abc"
    assert "outreach@mail.example.test" in m["From"] and "Fiona at Acme" in m["From"]
    assert m.get_body(("plain",)).get_content().strip() == "plain body"
    assert "html body" in m.get_body(("html",)).get_content()


def test_header_injection_through_names_and_subjects_is_neutralised():
    m = build_mime(_msg(subject="Hi\r\nBcc: evil@x.test", from_name="A\nBcc: evil@x.test"))
    assert m["Bcc"] is None
    assert "\n" not in str(m["Subject"]) and "\n" not in str(m["From"])


def test_configuration_gates(monkeypatch):
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    assert outreach_configured() is False
    with pytest.raises(OutreachNotConfiguredError):
        get_outreach_sender()
    configure(monkeypatch, provider="smtp")
    assert outreach_configured() is True and imap_configured() is True
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_PASSWORD", "")
    assert outreach_configured() is False  # one missing credential = not configured
    configure(monkeypatch, provider="smtp")
    monkeypatch.setattr(settings, "SALES_OUTREACH_FROM_EMAIL", "not-an-address")
    assert outreach_configured() is False


def test_console_provider_never_counts_as_configured_in_production(monkeypatch):
    configure(monkeypatch, provider="console")
    assert outreach_configured() is True
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert outreach_configured() is False
    configure(monkeypatch, provider="smtp")
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_SECURITY", "none")
    assert outreach_configured() is False  # plaintext SMTP is refused in production


def _smtp(monkeypatch, server):
    configure(monkeypatch, provider="smtp")
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_PORT", server.port)
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_SECURITY", "none")


def test_smtp_sender_delivers_to_the_fake_server(monkeypatch):
    with FakeSmtpServer() as server:
        _smtp(monkeypatch, server)
        assert SmtpOutreachSender().send(_msg()) == "<abc@mail.example.test>"
        rcpts, got = server.received[0]
    assert rcpts == ["buyer@prospect.test"]
    assert got["Reply-To"] == "fiona@acme.test" and got["List-Unsubscribe"]


def test_smtp_550_on_the_recipient_is_a_permanent_rejection(monkeypatch):
    with FakeSmtpServer() as server:
        server.reject["buyer@prospect.test"] = b"550 5.1.1 user unknown"
        _smtp(monkeypatch, server)
        with pytest.raises(OutreachRejectedError) as e:
            SmtpOutreachSender().send(_msg())
    assert e.value.code == "550"


def test_smtp_4xx_and_policy_blocks_are_transient_not_a_bounce(monkeypatch):
    with FakeSmtpServer() as server:
        _smtp(monkeypatch, server)
        for reply in (b"451 try later", b"554 policy block"):
            server.reject["buyer@prospect.test"] = reply
            with pytest.raises(OutreachUnavailableError):
                SmtpOutreachSender().send(_msg())


def test_unreachable_server_is_transient(monkeypatch):
    configure(monkeypatch, provider="smtp")
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_PORT", 1)  # nothing listens here
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_SECURITY", "none")
    with pytest.raises(OutreachUnavailableError):
        SmtpOutreachSender().send(_msg())
    assert outreach  # module import used
