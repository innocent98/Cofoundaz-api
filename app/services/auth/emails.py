"""Rendered account emails (verification, password reset, workspace invitation).

One place for the copy, branding, and link-building shared by the verification and
password-reset flows, so neither endpoint hand-builds HTML. The clickable link points
at the FRONTEND app (`APP_BASE_URL`, falling back to `SERVER_HOST`) at the routes the
FE serves (`/verify-email/{token}`, `/reset-password/{token}`, `/invite/{token}`); the FE page reads the
token from the URL and calls the API. The token also appears inside the link, so the
e2e `mailbox` fixture extracts it from the URL path (see `e2e/conftest.py::_TOKEN_RE`).
"""

from html import escape

from app.core.config import settings
from app.platform.email import EmailMessage


def _base_url() -> str:
    """The user-facing app origin for email links (FE), falling back to the API origin."""
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")


_BRAND_BLUE = "#0F6AFC"
_BRAND_NAVY = "#051736"


def _one_line(value: str) -> str:
    """A value that goes into a mail header (the subject): no line breaks, so a name typed by a
    founder cannot add headers."""
    return " ".join(value.split())


def _render(
    *,
    title: str,
    intro: str,
    cta_label: str,
    action_url: str,
    expiry_note: str,
    footer: str = "If you didn't request this, you can safely ignore this email.",
) -> str:
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a">'
        f'<h2 style="margin:0 0 12px;font-size:20px;color:{_BRAND_NAVY}">{title}</h2>'
        f'<p style="margin:0 0 20px;line-height:1.5;color:#333">{intro}</p>'
        '<p style="margin:0 0 20px">'
        f'<a href="{action_url}" style="display:inline-block;padding:12px 22px;'
        f"background:{_BRAND_BLUE};color:#ffffff;border-radius:8px;text-decoration:none;"
        f'font-weight:600">{cta_label}</a></p>'
        '<p style="margin:0 0 6px;font-size:13px;color:#555">'
        "Or paste this link into your browser:</p>"
        '<p style="margin:0 0 20px;font-size:13px;word-break:break-all">'
        f'<a href="{action_url}" style="color:{_BRAND_BLUE}">{action_url}</a></p>'
        f'<p style="margin:0 0 4px;font-size:12px;color:#888">{expiry_note}</p>'
        f'<p style="margin:0;font-size:12px;color:#888">{footer}</p>'
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


_ROLE_LABELS = {
    "founder": "Co-Founder",
    "team_member": "Team Member",
    "mentor": "Mentor",
    "accountant": "Accountant",
    "legal_advisor": "Legal Advisor",
    "business_consultant": "Business Consultant",
    "investor": "Investor",
}


def invitation_email(
    to: str, raw: str, *, startup_name: str | None, inviter_name: str | None, role: str
) -> EmailMessage:
    """The workspace invitation (14-day token). Links to the FE `/invite/{token}` landing,
    which previews the invite and lets the (signed-in, matching-email) invitee accept it."""
    workspace = escape(startup_name or "a startup")
    who = escape(inviter_name) if inviter_name else "A founder"
    action_url = f"{_base_url()}/invite/{raw}"
    return EmailMessage(
        to=to,
        subject=_one_line(
            f"{inviter_name or 'A founder'} invited you to join "
            f"{startup_name or 'a startup'} on Cofoundaz"
        ),
        html=_render(
            title=f"Join {workspace} on Cofoundaz",
            intro=(
                f"{who} invited you to join {workspace} as "
                f"{escape(_ROLE_LABELS.get(role, role))}."
            ),
            cta_label="Accept invitation",
            action_url=action_url,
            expiry_note="This invitation expires in 14 days.",
        ),
    )


def welcome_email(to: str, *, startup_name: str, full_name: str | None) -> EmailMessage:
    """Sent once, when a founder finishes onboarding (PRD Module 01 notifications)."""
    workspace = escape(startup_name)
    first = escape(full_name.split()[0]) if full_name and full_name.split() else None
    return EmailMessage(
        to=to,
        subject=_one_line(f"Welcome to Cofoundaz \u2014 let's build {startup_name}"),
        html=_render(
            title=f"Let's build {workspace}",
            intro=(
                f"{'Hi ' + first + ', your' if first else 'Your'} workspace is ready. "
                "Your roadmap and Health Score are being set up now. Take the Kickoff "
                "Assessment to sharpen them."
            ),
            cta_label="Open your dashboard",
            action_url=f"{_base_url()}/dashboard",
            expiry_note="Need a hand? Reply to this email.",
            footer="You're getting this because you created a Cofoundaz workspace.",
        ),
    )


def password_changed_email(to: str) -> EmailMessage:
    """Security notice after a password reset or change: the one signal that someone who is not
    the owner changed it (the link starts a reset)."""
    return EmailMessage(
        to=to,
        subject="Your Cofoundaz password was changed",
        html=_render(
            title="Your password was changed",
            intro=(
                "The password on your Cofoundaz account was just changed and you were signed "
                "out of your other devices. If this was you, there is nothing to do."
            ),
            cta_label="This wasn't me: reset my password",
            action_url=f"{_base_url()}/forgot-password",
            expiry_note="If it wasn't you, reset your password now.",
            footer="This is a security notice, so you get it even if you muted other emails.",
        ),
    )
