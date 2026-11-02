"""Rendered support emails. The founder gets one when staff reply and one when a ticket resolves."""

from html import escape

from app.core.config import settings
from app.platform.email import EmailMessage

SNIPPET = 1500


def _link(ticket_id: object) -> str:
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    return f"{base}/settings/help/{ticket_id}"


def _render(title: str, quote: str | None, url: str) -> str:
    quoted = ""
    if quote:
        body = escape(quote[:SNIPPET]).replace("\n", "<br>")
        quoted = (
            '<blockquote style="margin:0 0 20px;padding:8px 14px;border-left:3px solid #ccc;'
            f'color:#333;line-height:1.5">{body}</blockquote>'
        )
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a">'
        f'<h2 style="margin:0 0 12px;font-size:20px">{escape(title)}</h2>{quoted}'
        f'<p style="margin:0 0 20px"><a href="{url}" style="display:inline-block;padding:12px 22px;'
        'background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600">'
        "View ticket</a></p></div>"
    )


def staff_reply_email(
    to: str, ticket_no: int, subject: str, body: str, ticket_id: object
) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"Re: [T-{ticket_no}] {subject}",
        html=_render("The Cofoundaz team replied to your ticket", body, _link(ticket_id)),
    )


def resolved_email(
    to: str, ticket_no: int, subject: str, message: str | None, ticket_id: object
) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"Resolved: [T-{ticket_no}] {subject}",
        html=_render(
            "Your ticket was marked resolved. Reply on the ticket within 14 days to reopen it.",
            message,
            _link(ticket_id),
        ),
    )
