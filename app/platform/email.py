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


def get_email_sender() -> EmailSender:
    if settings.EMAIL_BACKEND == "smtp":
        return SMTPEmailSender()
    return ConsoleEmailSender()
