import uuid
from datetime import UTC, datetime

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.db.models.sales import SalesContact, SalesLead
from app.schemas.sales_sequences import EnrollIn, SequenceCreate, StepIn
from app.services.sales import sequences as seq_svc
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def configure(monkeypatch, *, provider: str = "console", hourly: int = 30) -> None:
    """Make the outreach mailbox look configured. Only setting NAMES are touched; the values are
    throwaway test strings and no connection is ever made."""
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", provider)
    monkeypatch.setattr(settings, "SALES_OUTREACH_FROM_EMAIL", "outreach@mail.example.test")
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_USERNAME", "test-user")
    monkeypatch.setattr(settings, "SALES_OUTREACH_SMTP_PASSWORD", "test-pass")
    monkeypatch.setattr(settings, "SALES_OUTREACH_IMAP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SALES_OUTREACH_HOURLY_LIMIT", hourly)


def workspace(db, *, name: str = "Acme", founder_email: str | None = None):
    u = create_user(db, email=founder_email, email_verified_at=NOW)
    u.profile.full_name = "Fiona Founder"
    s = create_startup(db, owner=u, name=name)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.commit()
    return u, s


def team_member(db, startup):
    u = create_user(db, email_verified_at=NOW)
    create_membership(db, u, startup, role=MembershipRole.team_member)
    db.commit()
    return u


def contact(db, startup, *, email: str | None = None, first: str = "Ada", last: str = "Obi"):
    c = SalesContact(
        startup_id=startup.id,
        first_name=first,
        last_name=last,
        email=email or f"c-{uuid.uuid4().hex[:8]}@prospect.test",
    )
    db.add(c)
    db.commit()
    return c


def lead(db, startup, *, email: str | None = None, name: str = "Bola Ade"):
    row = SalesLead(
        startup_id=startup.id,
        name=name,
        company="Bolt Co",
        email=email or f"l-{uuid.uuid4().hex[:8]}@prospect.test",
    )
    db.add(row)
    db.commit()
    return row


def sequence(db, startup, user, *, waits=(0, 3), stop_on_stage_change: bool = False):
    steps = [
        StepIn(
            wait_days=w,
            subject=f"Hi {{{{first_name}}}} step {i + 1}",
            body="Hello {{first_name}} from {{sender_name}} at {{company}}.",
        )
        for i, w in enumerate(waits)
    ]
    seq = seq_svc.create_sequence(
        db,
        startup.id,
        SequenceCreate(name="Cold intro", steps=steps, stop_on_stage_change=stop_on_stage_change),
        actor_id=user.id,
    )
    db.commit()
    return seq


def enroll_contacts(db, seq, user, *contacts):
    rows, skipped = seq_svc.enroll(
        db, seq, EnrollIn(contact_ids=[c.id for c in contacts]), actor_id=user.id
    )
    db.commit()
    return rows, skipped
