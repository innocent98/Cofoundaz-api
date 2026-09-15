import html
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.db.models.user import User
from app.platform.email import EmailMessage, get_email_sender
from app.services.notifications.categories import category_for
from app.worker.runner import register_handler

_CATEGORY_PATH = {
    "documents": "/documents",
    "business": "/business-builder",
    "roadmap_missions": "/roadmap",
    "health_assessment": "/health",
    "team": "/team",
}


def deep_link(notification: Notification) -> str:
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    if not base.startswith(("http://", "https://")):
        # Guard against a misconfigured/non-http base injecting an unexpected scheme
        # (e.g. javascript:) into the deep link. Static today, defense-in-depth for later.
        base = ""
    path = _CATEGORY_PATH.get(category_for(notification.type) or "", "/notifications")
    return f"{base}{path}"


def render_email(notification: Notification) -> str:
    link = html.escape(deep_link(notification), quote=True)
    title = html.escape(notification.title)
    body = html.escape(notification.body or "")
    return (
        f'<div style="font-family:system-ui,sans-serif;max-width:520px">'
        f"<h2>{title}</h2>"
        f"<p>{body}</p>"
        f'<p><a href="{link}" style="display:inline-block;padding:10px 16px;'
        f'background:#4f46e5;color:#fff;border-radius:6px;text-decoration:none">Open Cofoundaz</a></p>'
        f'<hr><p style="font-size:12px;color:#666">'
        f"Manage your notification preferences in Settings.</p></div>"
    )


def handle_email_notification(db: Session, job: Job) -> None:
    notification = db.get(Notification, uuid.UUID(str(job.payload["notification_id"])))
    if notification is None:
        return  # deleted since enqueue — nothing to send
    user = db.get(User, notification.user_id)
    if user is None or not user.email:
        return
    # Strip CR/LF to prevent header injection in the SMTP backend.
    subject = notification.title.replace("\r", " ").replace("\n", " ")
    get_email_sender().send(
        EmailMessage(to=user.email, subject=subject, html=render_email(notification))
    )


register_handler("email.notification", handle_email_notification)
