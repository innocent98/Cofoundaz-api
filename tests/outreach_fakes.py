"""Fakes for the outreach mailbox. NOTHING here (or in the tests that use it) ever connects to a
real mail server: the SMTP side is an in-process socket server on 127.0.0.1, the IMAP side a
stub object."""

import socketserver
import threading
import uuid
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from email.policy import default as default_policy

from app.platform.outreach import (
    OutreachMessage,
    OutreachRejectedError,
    OutreachUnavailableError,
)


class FakeSender:
    """In-memory `OutreachSender`. `fail` maps a recipient to an exception to raise."""

    def __init__(self) -> None:
        self.sent: list[OutreachMessage] = []
        self.fail: dict[str, Exception] = {}
        self.before_send = None  # optional hook, called with the message before it "sends"

    def send(self, msg: OutreachMessage) -> str:
        if self.before_send is not None:
            self.before_send(msg)
        if msg.to in self.fail:
            raise self.fail[msg.to]
        self.sent.append(msg)
        return msg.message_id

    @staticmethod
    def rejected(code: str = "550") -> OutreachRejectedError:
        return OutreachRejectedError(code, "no such user")

    @staticmethod
    def unavailable() -> OutreachUnavailableError:
        return OutreachUnavailableError("timeout")


@dataclass
class FakeMailbox:
    """Stub IMAP inbox: (uid, raw bytes) pairs; records what was marked processed."""

    messages: list[tuple[int, bytes]] = field(default_factory=list)
    uidvalidity: int = 1
    processed: list[int] = field(default_factory=list)
    closed: bool = False

    def fetch_after(self, last_uid: int) -> list[tuple[int, bytes]]:
        return [(u, raw) for u, raw in self.messages if u > last_uid]

    def mark_processed(self, uid: int) -> None:
        self.processed.append(uid)

    def close(self) -> None:
        self.closed = True


def dsn(
    *, send_id: uuid.UUID | None, recipient: str, status: str = "5.1.1", with_header: bool = True
) -> bytes:
    """A delivery-failure report (RFC 3464) quoting our original headers."""
    status_block = (
        f"Reporting-MTA: dns; mx.example.net\n\n"
        f"Final-Recipient: rfc822; {recipient}\nAction: failed\nStatus: {status}\n"
        f"Diagnostic-Code: smtp; 550 {status} user unknown\n"
    )
    original_headers = (
        f"Message-ID: <{send_id}@example.test>\n"
        + (f"X-Cofoundaz-Step: {send_id}\n" if with_header and send_id else "")
        + f"To: {recipient}\nSubject: Hello\n"
    )
    raw = (
        "From: Mail Delivery System <MAILER-DAEMON@mx.example.net>\n"
        "To: outreach@example.test\n"
        "Subject: Undelivered Mail Returned to Sender\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="rpt"\n\n'
        "--rpt\nContent-Type: text/plain\n\nYour message could not be delivered.\n\n"
        "--rpt\nContent-Type: message/delivery-status\n\n" + status_block + "\n"
        "--rpt\nContent-Type: text/rfc822-headers\n\n" + original_headers + "\n"
        "--rpt--\n"
    )
    return raw.encode()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # a tiny plain SMTP dialogue: enough for smtplib
        srv: FakeSmtpServer = self.server  # type: ignore[assignment]
        self.wfile.write(b"220 fake ESMTP\r\n")
        rcpts: list[str] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                self.wfile.write(b"250-fake\r\n250 AUTH PLAIN LOGIN\r\n")
            elif up.startswith("AUTH"):
                self.wfile.write(b"235 ok\r\n")
            elif up.startswith("MAIL FROM"):
                self.wfile.write(b"250 ok\r\n")
            elif up.startswith("RCPT TO"):
                addr = cmd.split("<", 1)[1].split(">", 1)[0].lower()
                if addr in srv.reject:
                    self.wfile.write(srv.reject[addr] + b"\r\n")
                else:
                    rcpts.append(addr)
                    self.wfile.write(b"250 ok\r\n")
            elif up == "DATA":
                self.wfile.write(b"354 go\r\n")
                data = b""
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b".\r\n", b""):
                        break
                    data += chunk
                srv.received.append((list(rcpts), message_from_bytes(data, policy=default_policy)))
                self.wfile.write(b"250 queued\r\n")
            elif up == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


class FakeSmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.received: list[tuple[list[str], Message]] = []
        self.reject: dict[str, bytes] = {}  # recipient -> full SMTP reply line to RCPT TO
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def __enter__(self) -> "FakeSmtpServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
        self.server_close()
