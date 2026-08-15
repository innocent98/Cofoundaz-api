from app.db.models.invitation import Invitation
from app.services.onboarding.invites import create_invitations
from tests.factories import create_startup, create_user


def test_intra_batch_duplicate_emails_dedupe_under_autoflush_false(db):
    """Prod's SessionLocal runs with autoflush=False (app/db/session.py). A per-item
    `db.query(...)` inside the loop would not see a same-email Invitation added earlier
    in the same call unless something flushes it first. Force that same condition here
    so the test fails the way it would in production if the in-memory dedupe guard were
    removed."""
    db.autoflush = False
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    db.flush()

    result = create_invitations(
        db,
        startup,
        owner,
        [
            {"email": "dup@x.com", "role": "mentor"},
            {"email": "dup@x.com", "role": "team_member"},
        ],
    )

    assert result == {"created": ["dup@x.com"], "skipped": ["dup@x.com"]}
    rows = db.query(Invitation).filter(Invitation.email == "dup@x.com").all()
    assert len(rows) == 1
