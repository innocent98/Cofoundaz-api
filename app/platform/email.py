from dataclasses import dataclass
from typing import Protocol

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

        m = emails.Message(
            subject=msg.subject,
            html=msg.html,
            mail_from=(settings.EMAILS_FROM_NAME, settings.EMAILS_FROM_EMAIL),
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
    if settings.EMAIL_BACKEND == "file":
        return FileEmailSender()
    return ConsoleEmailSender()
