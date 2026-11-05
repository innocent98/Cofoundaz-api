"""Provider emails (invite, submitted, changes requested, approved, rejected, suspended).

Providers get transactional email only in slice A (D-MP-L14); the in-portal inbox arrives with the
booking slices. Copy that carries staff-typed text escapes it. Emails are returned, not sent: the
caller sends them AFTER its commit so a rolled-back action never emails anyone."""

from html import escape

from app.core.logger import log
from app.platform.email import EmailMessage, get_email_sender
from app.services.auth.emails import _base_url, _render


def _portal() -> str:
    return f"{_base_url()}/provider"


def _greet(name: str | None) -> str:
    return f"Hi {escape(name)}," if name else "Hi,"


def provider_invite_email(
    to: str, raw: str, *, name: str | None, category: str | None
) -> EmailMessage:
    what = f" as a {escape(category)} expert" if category else " as an expert"
    return EmailMessage(
        to=to,
        subject="You're invited to join the Cofoundaz Marketplace",
        html=_render(
            title="Join the Cofoundaz Marketplace",
            intro=(
                f"{_greet(name)} Cofoundaz has invited you to offer your services{what} to "
                "founders on our Marketplace. Accept the invitation, complete your profile and "
                "upload your credentials, and our team will review your application."
            ),
            cta_label="Accept invitation",
            action_url=f"{_base_url()}/provider/invite/{raw}",
            expiry_note="This invitation expires in 14 days.",
        ),
    )


def application_received_email(to: str, *, name: str | None) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="We received your Marketplace application",
        html=_render(
            title="Application received",
            intro=(
                f"{_greet(name)} thanks for submitting your profile. Our team reviews every "
                "expert before they appear on the Marketplace. We'll email you when there's a "
                "decision or if we need anything more."
            ),
            cta_label="Open your expert portal",
            action_url=_portal(),
            expiry_note="Reviews usually take a few working days.",
        ),
    )


def changes_requested_email(to: str, *, name: str | None, message: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Changes needed on your Marketplace application",
        html=_render(
            title="A few changes are needed",
            intro=(
                f"{_greet(name)} our reviewer asked for some changes before we can approve "
                f"your profile: <br><br><em>{escape(message)}</em>"
            ),
            cta_label="Update your application",
            action_url=_portal(),
            expiry_note="Update your profile or documents, then submit again.",
        ),
    )


def approved_email(to: str, *, name: str | None) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="You're approved on the Cofoundaz Marketplace",
        html=_render(
            title="You're approved",
            intro=(
                f"{_greet(name)} your credentials have been verified and your profile is "
                "approved. You can now set up your services."
            ),
            cta_label="Open your expert portal",
            action_url=_portal(),
            expiry_note="Welcome aboard.",
        ),
    )


def rejected_email(to: str, *, name: str | None, label: str, message: str | None) -> EmailMessage:
    extra = f"<br><br><em>{escape(message)}</em>" if message else ""
    return EmailMessage(
        to=to,
        subject="Your Marketplace application",
        html=_render(
            title="We can't approve your application",
            intro=(
                f"{_greet(name)} after review we aren't able to approve your Marketplace "
                f"application. Reason: {escape(label)}.{extra} Uploaded credential documents "
                "have been deleted."
            ),
            cta_label="Contact support",
            action_url=f"{_base_url()}/support",
            expiry_note="If you think this is a mistake, contact support.",
        ),
    )


def suspended_email(to: str, *, name: str | None) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Your Marketplace profile is paused",
        html=_render(
            title="Your profile is paused",
            intro=(
                f"{_greet(name)} your Marketplace profile has been paused and is not visible "
                "to founders. Existing commitments will be honoured. Contact support to talk "
                "it through."
            ),
            cta_label="Contact support",
            action_url=f"{_base_url()}/support",
            expiry_note="You can still sign in to your expert portal.",
        ),
    )


def deliver(messages: list[EmailMessage]) -> list[str]:
    """Send after the caller's commit. A bad address is logged and reported, never raised: the
    action it announces already happened and the invite can be re-sent."""
    undelivered: list[str] = []
    sender = get_email_sender()
    for msg in messages:
        try:
            sender.send(msg)
        except Exception as exc:  # noqa: BLE001 - one bad address must not fail the action
            log.warning(f"[marketplace] email to {msg.to} failed: {exc}")
            undelivered.append(msg.to)
    return undelivered


def listing_approved_email(
    to: str, *, name: str | None, title: str, revision: bool
) -> EmailMessage:
    what = "your edit to" if revision else "your listing"
    return EmailMessage(
        to=to,
        subject="Your Marketplace listing is live" if not revision else "Your listing edit is live",
        html=_render(
            title="Approved",
            intro=f"{_greet(name)} {what} <strong>{escape(title)}</strong> has been approved and is live.",
            cta_label="Open your listings",
            action_url=f"{_portal()}/listings",
            expiry_note="Founders can find it in the Marketplace now.",
        ),
    )


def listing_feedback_email(
    to: str, *, name: str | None, title: str, headline: str, detail: str
) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"{headline}: {title}"[:120].replace("\n", " "),
        html=_render(
            title=headline,
            intro=(
                f"{_greet(name)} about <strong>{escape(title)}</strong>: <br><br>"
                f"<em>{escape(detail)}</em>"
            ),
            cta_label="Open your listings",
            action_url=f"{_portal()}/listings",
            expiry_note="Your live listing, if any, is unchanged.",
        ),
    )
