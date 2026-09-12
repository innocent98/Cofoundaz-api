from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.logger import log


@dataclass
class EmailMessage:
    to: str
    subject: str
    html: str


class EmailSender(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


class ConsoleEmailSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)
        log.info(f"[email:console] to={msg.to} subject={msg.subject!r}\n{msg.html}")


class SMTPEmailSender:
    def send(self, msg: EmailMessage) -> None:
        import emails  # lazy import

        # emails 1.x types mail_from as `str | tuple[str | None, str] | None`:
        # the display NAME may be None, the ADDRESS may not. Under 0.6 this was
        # untyped, so `(None, None)` type-checked and "worked" - except it does
        # not work. Verified against emails 1.1.2:
        #
        #   mail_from=(None, None)          -> From header absent entirely
        #   mail_from=(None, "a@b.com")     -> From: a@b.com
        #   mail_from=("Cofoundaz", "a@b.com") -> From: Cofoundaz <a@b.com>
        #
        # RFC 5322 requires From, so the first case produces a message that
        # every real MTA rejects or quarantines - and this code discards the
        # send result, so it fails silently. An unset EMAILS_FROM_EMAIL is
        # therefore a fatal misconfiguration, not a default to paper over, and
        # it is raised as one rather than cast away with a type: ignore.
        #
        # EMAILS_FROM_NAME needs no such handling: None is a supported value
        # and yields a bare-address From, which is valid.
        from_email = settings.EMAILS_FROM_EMAIL
        if from_email is None:
            raise RuntimeError(
                "EMAILS_FROM_EMAIL is not set, so the SMTP backend cannot build a From "
                "header and every message would be rejected by the receiving MTA. Set "
                "EMAILS_FROM_EMAIL (and optionally EMAILS_FROM_NAME). Deliberately not "
                "suggesting a different EMAIL_BACKEND as a workaround: `console` drops "
                "mail on the floor and `file` writes one-time tokens to disk, so both "
                "are dev-only."
            )

        m = emails.Message(
            subject=msg.subject,
            html=msg.html,
            mail_from=(settings.EMAILS_FROM_NAME, from_email),
        )
        m.send(
            to=msg.to,
            smtp={
                "host": settings.SMTP_HOST,
                "port": settings.SMTP_PORT,
                "tls": settings.SMTP_TLS,
                "user": settings.SMTP_USER,
                "password": settings.SMTP_PASSWORD,
            },
        )


class ResendEmailSender:
    """Sends via the Resend HTTP API (https://resend.com).

    Uses httpx (already a project dependency) rather than the `resend` SDK — the
    API is a single authenticated POST. Fails loud (raises) on a missing key,
    a missing From address, or a non-2xx response, mirroring SMTPEmailSender:
    a caller that must deliver (email verification, password reset) should see
    the failure, not have it swallowed. Callers that can tolerate a miss (e.g.
    a best-effort document-share notification) wrap the send themselves.
    """

    _ENDPOINT = "https://api.resend.com/emails"

    def send(self, msg: EmailMessage) -> None:
        api_key = settings.RESEND_API_KEY
        if not api_key:
            raise RuntimeError(
                "RESEND_API_KEY is not set, so the Resend backend cannot authenticate. "
                "Set RESEND_API_KEY (and EMAILS_FROM_EMAIL) or choose a different EMAIL_BACKEND."
            )
        from_email = settings.EMAILS_FROM_EMAIL
        if from_email is None:
            raise RuntimeError(
                "EMAILS_FROM_EMAIL is not set, so the Resend backend cannot build a From "
                "address. Set EMAILS_FROM_EMAIL (and optionally EMAILS_FROM_NAME)."
            )
        sender = (
            f"{settings.EMAILS_FROM_NAME} <{from_email}>"
            if settings.EMAILS_FROM_NAME
            else str(from_email)
        )
        response = httpx.post(
            self._ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": sender,
                "to": [msg.to],
                "subject": msg.subject,
                "html": msg.html,
            },
            timeout=10.0,
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"Resend API returned {response.status_code} sending to {msg.to}: {response.text}"
            )
        log.info(f"[email:resend] to={msg.to} subject={msg.subject!r}")


class FileEmailSender:
    """Writes each email as a JSON file so a black-box e2e/dev harness can read it back.

    Not for production: this persists full message bodies (including one-time
    tokens) to disk. Enable only via EMAIL_BACKEND=file in dev/e2e.
    """

    def __init__(self, mail_dir: str | None = None) -> None:
        self.mail_dir = mail_dir or settings.EMAIL_FILE_DIR

    def send(self, msg: EmailMessage) -> None:
        import json
        import uuid
        from datetime import UTC, datetime
        from pathlib import Path

        path = Path(self.mail_dir)
        path.mkdir(parents=True, exist_ok=True)
        payload = {
            "to": msg.to,
            "subject": msg.subject,
            "html": msg.html,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        (
            path / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}.json"
        ).write_text(json.dumps(payload))
        log.info(f"[email:file] to={msg.to} subject={msg.subject!r} -> {self.mail_dir}")


def get_email_sender() -> EmailSender:
    if settings.EMAIL_BACKEND == "smtp":
        return SMTPEmailSender()
    if settings.EMAIL_BACKEND == "resend":
        return ResendEmailSender()
    if settings.EMAIL_BACKEND == "file":
        return FileEmailSender()
    return ConsoleEmailSender()
