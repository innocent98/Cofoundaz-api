"""Sales outreach mail: the SMTP sender and the IMAP bounce mailbox (D-SAL-1).

A cPanel mailbox, deliberately NOT the Resend account that carries the app's auth email, so an
outreach reputation problem can never stop a signup or password-reset mail. Everything behind
`OutreachSender` / `BounceMailbox` is swappable: tests inject fakes, nothing here opens a socket
at import time, and every connection is made from the worker or a request that asked for one.
"""

import imaplib
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import format_datetime, formataddr
from typing import Protocol

from app.core.config import settings
from app.core.logger import log

SMTP_TIMEOUT_SECONDS = 20
IMAP_FETCH_LIMIT = 200


class OutreachNotConfiguredError(Exception):
    """The mailbox env is unset or incomplete: sequences still save, nothing sends."""


class OutreachRejectedError(Exception):
    """The server refused the recipient for good (5xx): treat the address as bounced."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code} {detail}")
        self.code = code
        self.detail = detail


class OutreachUnavailableError(Exception):
    """A transient failure (connect, timeout, 4xx): worth a retry later."""


@dataclass
class OutreachMessage:
    to: str
    from_email: str
    from_name: str
    reply_to: str
    subject: str
    text: str
    html: str
    message_id: str  # our own Message-ID, with angle brackets; bounce mail quotes it back
    headers: dict[str, str] = field(default_factory=dict)


class OutreachSender(Protocol):
    def send(self, msg: OutreachMessage) -> str:
        """Deliver `msg`; return an id for the ledger (the Message-ID). Raises
        OutreachRejectedError (permanent) or OutreachUnavailableError (transient)."""
        ...


def _one_line(value: str) -> str:
    """Header values are one line: strip CR/LF so a name or subject can never inject a header."""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def build_mime(msg: OutreachMessage) -> MimeMessage:
    mime = MimeMessage(policy=SMTP_POLICY)
    mime["From"] = formataddr((_one_line(msg.from_name), msg.from_email))
    mime["To"] = msg.to
    mime["Reply-To"] = msg.reply_to
    mime["Subject"] = _one_line(msg.subject)
    mime["Date"] = format_datetime(datetime.now(UTC))
    mime["Message-ID"] = msg.message_id
    for name, value in msg.headers.items():
        mime[name] = _one_line(value)
    mime.set_content(msg.text)
    mime.add_alternative(msg.html, subtype="html")
    return mime


def outreach_domain() -> str:
    return settings.SALES_OUTREACH_FROM_EMAIL.rpartition("@")[2].lower()


def outreach_configured() -> bool:
    """True only when a send could really happen: a provider is chosen and its env is complete.
    `console` only counts outside production (it delivers nothing)."""
    provider = settings.SALES_OUTREACH_PROVIDER.strip().lower()
    if not settings.SALES_OUTREACH_FROM_EMAIL or "@" not in settings.SALES_OUTREACH_FROM_EMAIL:
        return False
    if provider == "console":
        return settings.ENVIRONMENT != "production"
    if provider == "smtp":
        return bool(
            settings.SALES_OUTREACH_SMTP_HOST
            and settings.SALES_OUTREACH_SMTP_USERNAME
            and settings.SALES_OUTREACH_SMTP_PASSWORD
            and (
                settings.SALES_OUTREACH_SMTP_SECURITY != "none"
                or settings.ENVIRONMENT != "production"
            )
        )
    return False


def imap_configured() -> bool:
    return bool(
        outreach_configured()
        and settings.SALES_OUTREACH_PROVIDER.strip().lower() == "smtp"
        and settings.SALES_OUTREACH_IMAP_HOST
    )


class ConsoleOutreachSender:
    """Local dev: logs the message, delivers nothing. Keeps what it saw for inspection."""

    def __init__(self) -> None:
        self.sent: list[OutreachMessage] = []

    def send(self, msg: OutreachMessage) -> str:
        self.sent.append(msg)
        log.info(f"[outreach:console] to={msg.to} subject={msg.subject!r}")
        return msg.message_id


class SmtpOutreachSender:
    """Sends through the cPanel mailbox. Reads its settings at call time, never at import."""

    def send(self, msg: OutreachMessage) -> str:
        host = settings.SALES_OUTREACH_SMTP_HOST
        port = settings.SALES_OUTREACH_SMTP_PORT
        security = settings.SALES_OUTREACH_SMTP_SECURITY.strip().lower()
        mime = build_mime(msg)
        try:
            if security == "ssl":
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    host, port, timeout=SMTP_TIMEOUT_SECONDS, context=ssl.create_default_context()
                )
            else:
                client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
            with client:
                client.ehlo()
                if security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if settings.SALES_OUTREACH_SMTP_USERNAME:
                    client.login(
                        settings.SALES_OUTREACH_SMTP_USERNAME,
                        settings.SALES_OUTREACH_SMTP_PASSWORD,
                    )
                client.send_message(mime, from_addr=msg.from_email, to_addrs=[msg.to])
        except smtplib.SMTPRecipientsRefused as exc:
            code, detail = next(iter(exc.recipients.values()), (550, b"recipient refused"))
            self._classify(int(code), detail)
        except smtplib.SMTPResponseException as exc:
            self._classify(int(exc.smtp_code), exc.smtp_error)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise OutreachUnavailableError(type(exc).__name__) from exc
        return msg.message_id

    @staticmethod
    def _classify(code: int, detail: bytes | str) -> None:
        text = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
        if 500 <= code < 600 and code not in (530, 535, 554):
            # 530/535 are our own login being refused and 554 is often a policy block on the
            # mailbox, not a bad recipient: those must not suppress the contact.
            raise OutreachRejectedError(str(code), text[:200])
        raise OutreachUnavailableError(f"{code} {text[:120]}")


def get_outreach_sender() -> OutreachSender:
    if not outreach_configured():
        raise OutreachNotConfiguredError()
    if settings.SALES_OUTREACH_PROVIDER.strip().lower() == "console":
        return _CONSOLE
    return SmtpOutreachSender()


_CONSOLE = ConsoleOutreachSender()


class BounceMailbox(Protocol):
    """Read side of the outreach mailbox. UIDs are only meaningful within one UIDVALIDITY."""

    uidvalidity: int

    def fetch_after(self, last_uid: int) -> list[tuple[int, bytes]]: ...

    def mark_processed(self, uid: int) -> None: ...

    def close(self) -> None: ...


class ImapBounceMailbox:
    """INBOX of the cPanel mailbox. Not verified against a real server (see the SOP)."""

    def __init__(self) -> None:
        host = settings.SALES_OUTREACH_IMAP_HOST
        port = settings.SALES_OUTREACH_IMAP_PORT
        try:
            if port == 993:
                self._conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    host,
                    port,
                    ssl_context=ssl.create_default_context(),
                    timeout=SMTP_TIMEOUT_SECONDS,
                )
            else:
                self._conn = imaplib.IMAP4(host, port, timeout=SMTP_TIMEOUT_SECONDS)
                self._conn.starttls(ssl_context=ssl.create_default_context())
            self._conn.login(
                settings.SALES_OUTREACH_SMTP_USERNAME, settings.SALES_OUTREACH_SMTP_PASSWORD
            )
            self._conn.select("INBOX")
            _, data = self._conn.response("UIDVALIDITY")
            self.uidvalidity = int(data[0]) if data and data[0] else 0
        except (imaplib.IMAP4.error, OSError, ssl.SSLError, ValueError) as exc:
            raise OutreachUnavailableError(type(exc).__name__) from exc

    def fetch_after(self, last_uid: int) -> list[tuple[int, bytes]]:
        typ, data = self._conn.uid("SEARCH", "UID", f"{last_uid + 1}:*")
        if typ != "OK" or not data or not data[0]:
            return []
        # `n:*` always returns the highest UID even when it is below n, so filter explicitly.
        uids = sorted(int(u) for u in data[0].split() if int(u) > last_uid)[:IMAP_FETCH_LIMIT]
        out: list[tuple[int, bytes]] = []
        for uid in uids:
            typ, parts = self._conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                break  # stop here: later UIDs must not be ledgered past a message we failed to read
            out.append((uid, bytes(parts[0][1])))
        return out

    def mark_processed(self, uid: int) -> None:
        """Mark a handled bounce. NEVER deletes: the mailbox may be a shared inbox. The message is
        copied into the processed folder (best effort) and flagged read + flagged in place. The
        (UIDVALIDITY, UID) ledger, not these flags, is what guarantees it is not counted twice."""
        folder = settings.SALES_OUTREACH_IMAP_PROCESSED_FOLDER
        try:
            self._conn.create(folder)  # fails harmlessly when it already exists
            self._conn.uid("COPY", str(uid), folder)
        except imaplib.IMAP4.error as exc:
            log.warning(f"[outreach] could not copy bounce uid={uid}: {exc}")
        try:
            self._conn.uid("STORE", str(uid), "+FLAGS", "(\\Seen \\Flagged)")
        except imaplib.IMAP4.error as exc:
            log.warning(f"[outreach] could not flag bounce uid={uid}: {exc}")

    def close(self) -> None:
        try:
            self._conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass  # closing a connection that is already gone is fine


def get_bounce_mailbox() -> BounceMailbox:
    if not imap_configured():
        raise OutreachNotConfiguredError()
    return ImapBounceMailbox()
