from typing import Any

import pytest

from app.core.config import settings
from app.platform import outreach
from app.platform.outreach import (
    OutreachAmbiguousError,
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


def test_every_failure_class_before_the_recipient_is_accepted_is_transient(monkeypatch):
    with FakeSmtpServer() as server:
        _smtp(monkeypatch, server)
        server.mail_reply = (
            b"550 5.7.1 sender not allowed"  # SMTPSenderRefused: not the contact's fault
        )
        with pytest.raises(OutreachUnavailableError):
            SmtpOutreachSender().send(_msg())
        server.mail_reply = b"250 ok"
        server.reject["buyer@prospect.test"] = b"452 mailbox full"
        with pytest.raises(OutreachUnavailableError):
            SmtpOutreachSender().send(_msg())
        for reply in (b"530 auth needed", b"535 bad login", b"554 policy"):
            server.reject["buyer@prospect.test"] = reply
            with pytest.raises(OutreachUnavailableError):
                SmtpOutreachSender().send(_msg())
        assert server.received == []


@pytest.mark.parametrize("reply", [b"554 spam detected", b"552 quota exceeded", b"451 try later"])
def test_a_refused_message_after_data_is_transient_never_a_bad_address(monkeypatch, reply):
    with FakeSmtpServer() as server:
        _smtp(monkeypatch, server)
        server.data_reply = reply  # SMTPDataError class
        with pytest.raises(OutreachUnavailableError):
            SmtpOutreachSender().send(_msg())


def test_losing_the_connection_once_data_started_is_ambiguous(monkeypatch):
    with FakeSmtpServer() as server:
        _smtp(monkeypatch, server)
        server.drop_after_data = True
        with pytest.raises(OutreachAmbiguousError):
            SmtpOutreachSender().send(_msg())
        assert len(server.received) == 1  # the server really did get the mail


def test_a_failing_quit_after_a_250_does_not_undo_the_send(monkeypatch):
    with FakeSmtpServer() as server:
        _smtp(monkeypatch, server)
        server.drop_on_quit = True
        assert SmtpOutreachSender().send(_msg()) == "<abc@mail.example.test>"


def test_to_and_reply_to_cannot_inject_headers():
    m = build_mime(_msg(to="a@b.test\r\nBcc: evil@x.test", reply_to="c@d.test\nBcc: evil@x.test"))
    assert m["Bcc"] is None and "\n" not in str(m["To"]) and "\n" not in str(m["Reply-To"])


class _Conn:
    """Stub imaplib connection recording every command."""

    def __init__(self, messages):
        self.messages, self.calls = messages, []

    def uid(self, cmd, *args):
        self.calls.append((cmd, *args))
        if cmd == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in self.messages)]
        if cmd == "FETCH":
            uid, what = int(args[0]), args[1]
            head, body = self.messages[uid]
            return "OK", [(b"x", head if "HEADER" in what else head + body)]
        return "OK", [b""]

    def create(self, *a):
        self.calls.append(("CREATE", *a))

    def expunge(self):
        self.calls.append(("EXPUNGE",))


def test_imap_peeks_headers_and_pulls_full_bodies_only_for_delivery_reports():
    from app.platform.outreach import ImapBounceMailbox

    box = ImapBounceMailbox.__new__(ImapBounceMailbox)
    report = b'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\r\n\r\n'
    plain = b"Content-Type: text/plain\r\nSubject: hello\r\n\r\n"
    box._conn = _Conn({5: (plain, b"private body"), 6: (report, b"--b--")})
    out = dict(box.fetch_after(0))
    assert out[5] == plain and b"private body" not in out[5]  # normal mail: headers only
    assert out[6].endswith(b"--b--")  # a delivery report: full body
    fetched = [c for c in box._conn.calls if c[0] == "FETCH"]
    assert all("PEEK" in c[2] for c in fetched)  # nothing is marked read by reading


def test_imap_never_deletes_or_expunges():
    from app.platform.outreach import ImapBounceMailbox

    box = ImapBounceMailbox.__new__(ImapBounceMailbox)
    box._conn = _Conn({})
    box.mark_processed(9)
    flat = " ".join(str(c) for c in box._conn.calls)
    assert "Deleted" not in flat and "EXPUNGE" not in flat and "Flagged" in flat
