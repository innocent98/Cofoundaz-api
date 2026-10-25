"""The activity audience policy (D-MIS-8): the SQL filter used by GET /dashboard/activity and
the Python check used by the live stream must agree, and every action the app writes must be
classified on purpose rather than falling through to the fail-closed default."""

import re
from pathlib import Path

import pytest

from app.db.models.activity import ActivityLog
from app.db.models.enums import MembershipRole
from app.platform import activity as policy
from tests.factories import create_activity, create_startup, create_user

_SAMPLES = [
    ("mission.task.added", "mission_task"),
    ("mission.task.completed", None),
    ("finance.runway.low", None),
    ("marketing.post.published", "post"),
    ("roadmap.milestone.completed", "roadmap_milestone"),
    ("roadmap.task.linked", "mission_task"),
    ("healthscore.recomputed", None),
    ("business.plan.generated", "invoice"),
    ("document.signature.completed", "document"),
    ("assessment.completed", "assessment"),
    ("member.joined", "membership"),
    ("system.sync", None),
    ("nodots", None),
]


@pytest.mark.parametrize("role", list(MembershipRole))
def test_sql_filter_matches_python_check(db, role):
    owner = create_user(db)
    s = create_startup(db, owner=owner)
    for action, entity_type in _SAMPLES:
        create_activity(db, startup=s, action=action, entity_type=entity_type, summary=action)
    db.flush()

    via_sql = {
        a
        for (a,) in db.query(ActivityLog.action)
        .filter(ActivityLog.startup_id == s.id, policy.activity_visibility_filter(role))
        .all()
    }
    via_python = {a for a, e in _SAMPLES if policy.activity_visible(role, a, e)}
    assert via_sql == via_python


def _written_actions() -> set[str]:
    actions: set[str] = set()
    for path in Path("app").rglob("*.py"):
        src = path.read_text()
        if "write_activity(" in src and path.name != "activity.py":
            actions |= set(re.findall(r'action="([a-z_.]+)"', src))
    return actions


def test_every_written_action_is_classified():
    actions = _written_actions()
    assert "mission.task.added" in actions  # the scan really found the writers
    classified = policy._OPEN_NAMESPACES | set(policy._RESTRICTED_NAMESPACES)
    unclassified = {a for a in actions if a.split(".", 1)[0] not in classified}
    assert unclassified == set(), "classify these in app/platform/activity.py"


@pytest.mark.parametrize("role", list(MembershipRole))
def test_document_activity_is_founder_and_team_member_only(role):
    """D-DOC-1: document activity names documents and signers; same gate as the Documents routes."""
    seen = policy.activity_visible(role, "document.signature.completed", "signature_request")
    assert seen == (role in {MembershipRole.founder, MembershipRole.team_member})
