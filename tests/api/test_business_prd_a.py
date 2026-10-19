"""Business Builder PRD completion, slice (a): last edited, version history + restore (D-BB-1),
Export to a Document (D-BB-2), consultant-only suggest mode (D-BB-3), PRD notification copy,
overview metadata and the record field-type descriptors."""

from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.business import BusinessCanvasVersion, BusinessPlan
from app.db.models.document import Document
from app.db.models.enums import BusinessPlanStatus, MembershipRole, StartupStage
from app.platform.events import event_bus
from app.services.notifications.registry import SPECS
from tests.factories import create_membership, create_startup, create_user

BB = "/api/v1/business-builder"


def _headers(u, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def _named(db, name, **kw):
    u = create_user(db, email_verified_at=datetime.now(UTC), **kw)
    u.profile.full_name = name
    return u


def _workspace(db, *roles):
    """A workspace owned by a founder plus one member per extra role. Returns (startup, [(user, headers)])."""
    founder = _named(db, "Ada Founder")
    s = create_startup(db, owner=founder, stage=StartupStage.validation)
    create_membership(db, founder, s, role=MembershipRole.founder)
    people = [(founder, _headers(founder, s))]
    for i, role in enumerate(roles):
        u = _named(db, f"Bea {role.value.title()}", email=f"m{i}-{role.value}@x.com")
        create_membership(db, u, s, role=role)
        people.append((u, _headers(u, s)))
    db.flush()
    return s, people


def _save(client, h, ctype, blocks, version):
    r = client.put(f"{BB}/canvases/{ctype}", json={"blocks": blocks, "version": version}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# --- last edited -----------------------------------------------------------------------------


def test_canvas_carries_last_edited_by_and_at(client, db):
    _s, [(founder, h)] = _workspace(db)
    fresh = client.get(f"{BB}/canvases/swot", headers=h).json()["data"]
    assert fresh["updated_by"] is None
    assert fresh["updated_at"]
    saved = _save(client, h, "swot", {"strengths": ["Team"]}, 1)
    assert saved["updated_by"] == {"id": str(founder.id), "name": "Ada Founder"}


# --- versions (D-BB-1) ----------------------------------------------------------------------


def test_each_save_snapshots_a_version_newest_first(client, db):
    _s, [(founder, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    _save(client, h, "swot", {"strengths": ["A"]}, 1)
    _save(client, h, "swot", {"strengths": ["A", "B"]}, 2)
    r = client.get(f"{BB}/canvases/swot/versions", headers=h)
    assert r.status_code == 200
    rows = r.json()["data"]["versions"]
    assert [v["version"] for v in rows] == [3, 2]
    assert rows[0]["source"] == "manual"
    assert rows[0]["edited_by"] == {"id": str(founder.id), "name": "Ada Founder"}
    one = client.get(f"{BB}/canvases/swot/versions/2", headers=h)
    assert one.status_code == 200
    assert one.json()["data"]["blocks"]["strengths"] == ["A"]


def test_unknown_version_is_404(client, db):
    _s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    assert client.get(f"{BB}/canvases/swot/versions/99", headers=h).status_code == 404


def test_restore_writes_a_new_version_from_the_snapshot(client, db):
    _s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    _save(client, h, "swot", {"strengths": ["Old"]}, 1)
    _save(client, h, "swot", {"strengths": ["New"]}, 2)
    r = client.post(f"{BB}/canvases/swot/versions/2/restore", json={"version": 3}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["version"] == 4
    assert data["blocks"]["strengths"] == ["Old"]
    latest = client.get(f"{BB}/canvases/swot/versions", headers=h).json()["data"]["versions"][0]
    assert latest["version"] == 4 and latest["source"] == "restore"


def test_restore_with_stale_version_is_409(client, db):
    _s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    _save(client, h, "swot", {"strengths": ["Old"]}, 1)
    _save(client, h, "swot", {"strengths": ["New"]}, 2)
    r = client.post(f"{BB}/canvases/swot/versions/2/restore", json={"version": 2}, headers=h)
    assert r.status_code == 409


def test_consultant_cannot_restore(client, db):
    _s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    client.get(f"{BB}/canvases/swot", headers=h)
    _save(client, h, "swot", {"strengths": ["Old"]}, 1)
    r = client.post(f"{BB}/canvases/swot/versions/2/restore", json={"version": 2}, headers=h_bc)
    assert r.status_code == 403


def test_version_history_is_capped_at_50(client, db):
    _s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    for i in range(52):
        _save(client, h, "swot", {"strengths": [f"s{i}"]}, i + 1)
    rows = client.get(f"{BB}/canvases/swot/versions", headers=h).json()["data"]["versions"]
    assert len(rows) == 50
    assert rows[0]["version"] == 53
    assert db.query(BusinessCanvasVersion).count() == 50


def test_approved_suggestion_snapshots_with_the_author(client, db):
    _s, [(_f, h), (bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    client.get(f"{BB}/canvases/swot", headers=h)
    sid = client.post(
        f"{BB}/suggestions",
        json={
            "op": "canvas_update",
            "target": {"canvas_type": "swot"},
            "payload": {"blocks": {"threats": ["Incumbent"]}},
        },
        headers=h_bc,
    ).json()["data"]["id"]
    assert client.post(f"{BB}/suggestions/{sid}/approve", headers=h).status_code == 200
    latest = client.get(f"{BB}/canvases/swot/versions", headers=h).json()["data"]["versions"][0]
    assert latest["source"] == "suggestion"
    assert latest["edited_by"]["id"] == str(bc.id)


# --- export (D-BB-2) ------------------------------------------------------------------------


def test_export_canvas_creates_a_document(client, db):
    _s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    _save(client, h, "swot", {"strengths": ["Team", "Speed"], "threats": ["Incumbent"]}, 1)
    r = client.post(f"{BB}/canvases/swot/export", headers=h)
    assert r.status_code == 201, r.text
    doc_id = r.json()["data"]["document_id"]
    doc = db.get(Document, doc_id)
    assert doc is not None
    assert "SWOT" in doc.title
    headings = [sec["heading"] for sec in doc.sections]
    assert headings[:2] == ["Strengths", "Weaknesses"]
    assert "Team" in doc.sections[0]["body"] and "Speed" in doc.sections[0]["body"]


def test_export_records_creates_a_document(client, db):
    _s, [(_f, h)] = _workspace(db)
    created = client.post(
        f"{BB}/competitors",
        json={"data": {"name": "Paystack", "positioning": "Payments for Africa"}},
        headers=h,
    )
    assert created.status_code == 201, created.text
    r = client.post(f"{BB}/competitors/export", headers=h)
    assert r.status_code == 201, r.text
    doc = db.get(Document, r.json()["data"]["document_id"])
    assert doc.sections[0]["heading"] == "Paystack"
    assert "Payments for Africa" in doc.sections[0]["body"]


def test_export_of_an_empty_artifact_is_422(client, db):
    _s, [(_f, h)] = _workspace(db)
    r = client.post(f"{BB}/canvases/lean/export", headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "NOTHING_TO_EXPORT"
    assert client.post(f"{BB}/personas/export", headers=h).status_code == 422


def test_export_unknown_artifact_404_and_consultant_403(client, db):
    _s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    assert client.post(f"{BB}/canvases/nope/export", headers=h).status_code == 404
    assert client.post(f"{BB}/nope/export", headers=h).status_code == 404
    assert client.post(f"{BB}/canvases/swot/export", headers=h_bc).status_code == 403


# --- suggest mode (D-BB-3) + notifications ---------------------------------------------------


def test_only_a_consultant_can_suggest(client, db):
    _s, [(_f, h), (_m, h_mentor), (_tm, h_tm)] = _workspace(
        db, MembershipRole.mentor, MembershipRole.team_member
    )
    body = {
        "op": "canvas_update",
        "target": {"canvas_type": "swot"},
        "payload": {"blocks": {"threats": ["X"]}},
    }
    assert client.post(f"{BB}/suggestions", json=body, headers=h_mentor).status_code == 403
    assert client.post(f"{BB}/suggestions", json=body, headers=h_tm).status_code == 403
    assert client.post(f"{BB}/suggestions", json=body, headers=h).status_code == 403


def test_suggestion_notifies_founders_only_with_prd_copy(client, db):
    s, [(founder, h), (bc, h_bc), (_tm, _h_tm)] = _workspace(
        db, MembershipRole.business_consultant, MembershipRole.team_member
    )
    before = len(event_bus.published)
    r = client.post(
        f"{BB}/suggestions",
        json={
            "op": "canvas_update",
            "target": {"canvas_type": "business_model"},
            "payload": {"blocks": {"key_partners": ["Acme"]}},
        },
        headers=h_bc,
    )
    assert r.status_code == 201
    ((_e, payload),) = [
        e for e in event_bus.published[before:] if e[0] == "business.suggestion.created"
    ]
    spec = SPECS["business.suggestion.created"]
    assert spec.recipients(db, payload) == [founder.id]
    assert (
        spec.title(payload) == "Bea Business_Consultant suggested changes to Business Model Canvas"
    )
    assert spec.body(payload) == "Review suggestions"


def test_plan_notification_uses_prd_copy():
    spec = SPECS["business.plan.generated"]
    assert spec.title({}) == "Your AI-generated business plan is ready"
    assert spec.body({}) == "Open it"


# --- overview + field descriptors -----------------------------------------------------------


def test_overview_carries_last_edited_and_a_plan_row(client, db):
    s, [(founder, h)] = _workspace(db)
    rows = {r["type"]: r for r in client.get(f"{BB}/overview", headers=h).json()["data"]}
    assert rows["swot"]["updated_at"] is None
    assert rows["persona"]["updated_at"] is None
    assert rows["plan"]["status"] == "none" and rows["plan"]["document_id"] is None

    client.get(f"{BB}/canvases/swot", headers=h)
    _save(client, h, "swot", {"strengths": ["Team"]}, 1)
    client.post(f"{BB}/personas", json={"data": {"name": "Ngozi"}}, headers=h)
    db.add(
        BusinessPlan(
            startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=founder.id
        )
    )
    db.flush()
    rows = {r["type"]: r for r in client.get(f"{BB}/overview", headers=h).json()["data"]}
    assert rows["swot"]["updated_at"] and rows["swot"]["status"] == "continue"
    assert rows["persona"]["updated_at"]
    assert rows["plan"]["status"] == "generating"


def test_record_field_types_are_readable(client, db):
    _s, [(_f, h)] = _workspace(db)
    fields = {f["key"]: f for f in client.get(f"{BB}/personas", headers=h).json()["data"]["fields"]}
    assert fields["name"]["type"] == "str"
    assert fields["goals"]["type"] == "list[str]"
