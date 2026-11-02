import html
import uuid
from dataclasses import dataclass

from app.core.config import settings
from app.platform.outreach import OutreachMessage, outreach_domain
from app.services.sales.sequences import render_tags
from app.services.sales.tokens import sign_send


def fe_base() -> str:
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")


def api_base() -> str:
    return (settings.API_PUBLIC_URL or settings.SERVER_HOST).rstrip("/")


def unsubscribe_page_url(token: str) -> str:
    return f"{fe_base()}/unsubscribe/{token}"


def unsubscribe_api_url(token: str) -> str:
    return f"{api_base()}{settings.API_V1_STR}/public/sales/unsubscribe/{token}"


def message_id_for(send_id: uuid.UUID) -> str:
    return f"<{send_id}@{outreach_domain() or 'localhost'}>"


@dataclass
class RenderInput:
    send_id: uuid.UUID
    to: str
    subject: str  # raw template (or already-final text for a follow-up, with no tags in it)
    body: str
    first_name: str
    last_name: str
    company: str
    sender_name: str
    sender_email: str
    startup_name: str
    postal_address: str | None


def _paragraphs(text: str) -> str:
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    return "".join(f"<p>{html.escape(b).replace(chr(10), '<br>')}</p>" for b in blocks)


def render_message(r: RenderInput) -> OutreachMessage:
    """Merge tags, then the footer the member cannot edit: who we are, where to write, and a
    working unsubscribe link (also exposed as the List-Unsubscribe header, RFC 8058)."""
    values = {
        "first_name": r.first_name,
        "last_name": r.last_name,
        "company": r.company,
        "sender_name": r.sender_name,
    }
    subject = render_tags(r.subject, values).strip()
    body = render_tags(r.body, values).strip()
    token = sign_send(r.send_id)
    page = unsubscribe_page_url(token)
    footer_lines = [r.startup_name]
    if r.postal_address:
        footer_lines.append(r.postal_address)
    text = body + "\n\n--\n" + "\n".join(footer_lines) + f"\nUnsubscribe: {page}\n"
    footer_html = "<br>".join(html.escape(line) for line in footer_lines)
    html_body = (
        _paragraphs(body)
        + '<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 12px">'
        + f'<p style="color:#666;font-size:12px">{footer_html}<br>'
        + f'<a href="{html.escape(page, quote=True)}">Unsubscribe</a></p>'
    )
    from_name = f"{r.sender_name} at {r.startup_name}"
    return OutreachMessage(
        to=r.to,
        from_email=settings.SALES_OUTREACH_FROM_EMAIL,
        from_name=from_name,
        reply_to=r.sender_email,
        subject=subject,
        text=text,
        html=html_body,
        message_id=message_id_for(r.send_id),
        headers={
            "X-Cofoundaz-Step": str(r.send_id),
            "List-Unsubscribe": f"<{unsubscribe_api_url(token)}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
