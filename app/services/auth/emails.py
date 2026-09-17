"""Rendered auth emails (verification, password reset).

One place for the copy, branding, and link-building shared by the verification and
password-reset flows, so neither endpoint hand-builds HTML. The clickable link points
at the FRONTEND app (`APP_BASE_URL`, falling back to `SERVER_HOST`) at the routes the
FE serves (`/verify-email/{token}`, `/reset-password/{token}`); the FE page reads the
token from the URL and calls the API. The token also appears inside the link, so the
e2e `mailbox` fixture extracts it from the URL path (see `e2e/conftest.py::_TOKEN_RE`).
"""

from app.core.config import settings
from app.platform.email import EmailMessage


def _base_url() -> str:
    """The user-facing app origin for email links (FE), falling back to the API origin."""
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")


def _render(*, title: str, intro: str, cta_label: str, action_url: str, expiry_note: str) -> str:
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a">'
        f'<h2 style="margin:0 0 12px;font-size:20px">{title}</h2>'
        f'<p style="margin:0 0 20px;line-height:1.5;color:#333">{intro}</p>'
        '<p style="margin:0 0 20px">'
        f'<a href="{action_url}" style="display:inline-block;padding:12px 22px;'
        "background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;"
        f'font-weight:600">{cta_label}</a></p>'
        '<p style="margin:0 0 6px;font-size:13px;color:#555">'
        "Or paste this link into your browser:</p>"
        '<p style="margin:0 0 20px;font-size:13px;word-break:break-all">'
        f'<a href="{action_url}" style="color:#4f46e5">{action_url}</a></p>'
        f'<p style="margin:0 0 4px;font-size:12px;color:#888">{expiry_note}</p>'
        '<p style="margin:0;font-size:12px;color:#888">'
        "If you didn't request this, you can safely ignore this email.</p>"
        "</div>"
    )


def verification_email(to: str, raw: str) -> EmailMessage:
    """The email that confirms a new registration's address (24h token, spec Module 01)."""
    action_url = f"{_base_url()}/verify-email/{raw}"
    return EmailMessage(
        to=to,
        subject="Verify your email",
        html=_render(
            title="Verify your email",
            intro="Welcome to Cofoundaz! Confirm your email address to activate your account.",
            cta_label="Verify email",
            action_url=action_url,
            expiry_note="This link expires in 24 hours.",
        ),
    )


def password_reset_email(to: str, raw: str) -> EmailMessage:
    """The email that lets a user set a new password (1h token, spec Module 01)."""
    action_url = f"{_base_url()}/reset-password/{raw}"
    return EmailMessage(
        to=to,
        subject="Reset your password",
        html=_render(
            title="Reset your password",
            intro=(
                "We received a request to reset your Cofoundaz password. " "Choose a new one below."
            ),
            cta_label="Reset password",
            action_url=action_url,
            expiry_note="This link expires in 1 hour.",
        ),
    )
