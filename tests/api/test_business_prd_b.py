"""Business Builder PRD completion, slice (b): section comment threads, per-section AI assist
(append-only, D-BB-4), Mission & Vision "Give me 3 options", personas from validation notes and
the pricing AI advisor."""

import uuid
from datetime import UTC, date, datetime

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.business import BusinessRecord
from app.db.models.enums import (
    InterviewVerdict,
    MembershipRole,
    RecordKind,
    StartupStage,
)
from app.db.models.job import Job
from app.db.models.validation import Interview
from app.services.business.service import get_or_create_canvas, list_versions
from app.worker.handlers.ai import (
    handle_canvas_ai_fill,
    handle_mission_vision_options,
    handle_personas_from_validation,
    handle_pricing_advisor,
)
from tests.factories import create_membership, create_startup, create_user

BB = "/api/v1/business-builder"


def _headers(u, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def _workspace(db, *roles):
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=founder, stage=StartupStage.validation)
    create_membership(db, founder, s, role=MembershipRole.founder)
    people = [(founder, _headers(founder, s))]
    for role in roles:
        u = create_user(db, email_verified_at=datetime.now(UTC))
        create_membership(db, u, s, role=role)
        people.append((u, _headers(u, s)))
    db.flush()
    return s, people


def _job_of(db, r):
    assert r.status_code == 202, r.text
    return db.get(Job, uuid.UUID(r.json()["data"]["job_id"]))


# --- comments on canvas sections and records ---------------------------------------------------


def test_comment_on_a_canvas_section_and_list_grouped(client, db):
    s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    canvas = client.get(f"{BB}/canvases/swot", headers=h).json()["data"]
    canvas_id = get_or_create_canvas(db, s, canvas_type_of("swot")).id
    assert canvas["id"] == str(canvas_id)  # the FE gets the thread id from the canvas body
    r = client.post(
        "/api/v1/team/comments",
        json={
            "entity_type": "business_canvas",
            "entity_id": str(canvas_id),
            "anchor": "strengths",
            "body": "Is speed really a strength yet?",
        },
        headers=h_bc,
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["anchor"] == "strengths"
    client.post(
        "/api/v1/team/comments",
        json={"entity_type": "business_canvas", "entity_id": str(canvas_id), "body": "Overall"},
        headers=h,
    )
    everything = client.get(
        "/api/v1/team/comments",
        params={"entity_type": "business_canvas", "entity_id": str(canvas_id)},
        headers=h,
    ).json()["data"]
    assert {c["anchor"] for c in everything} == {"strengths", None}
    one = client.get(
        "/api/v1/team/comments",
        params={
            "entity_type": "business_canvas",
            "entity_id": str(canvas_id),
            "anchor": "strengths",
        },
        headers=h,
    ).json()["data"]
    assert len(one) == 1
    assert canvas["type"] == "swot"


def canvas_type_of(value):
    from app.db.models.enums import CanvasType

    return CanvasType(value)


def test_unknown_anchor_is_422_and_anchor_on_roadmap_is_422(client, db):
    s, [(_f, h)] = _workspace(db)
    canvas_id = get_or_create_canvas(db, s, canvas_type_of("swot")).id
    db.flush()
    bad = client.post(
        "/api/v1/team/comments",
        json={
            "entity_type": "business_canvas",
            "entity_id": str(canvas_id),
            "anchor": "key_partners",
            "body": "x",
        },
        headers=h,
    )
    assert bad.status_code == 422
    rm = client.post(
        "/api/v1/team/comments",
        json={
            "entity_type": "roadmap_task",
            "entity_id": str(uuid.uuid4()),
            "anchor": "x",
            "body": "x",
        },
        headers=h,
    )
    assert rm.status_code == 422


def test_comment_on_a_record_and_cross_tenant_404(client, db):
    _s, [(_f, h)] = _workspace(db)
    rec = client.post(f"{BB}/personas", json={"data": {"name": "Ngozi"}}, headers=h).json()["data"]
    ok = client.post(
        "/api/v1/team/comments",
        json={"entity_type": "business_record", "entity_id": rec["id"], "body": "Real person?"},
        headers=h,
    )
    assert ok.status_code == 201, ok.text
    _s2, [(_f2, h_other)] = _workspace(db)
    other = client.post(
        "/api/v1/team/comments",
        json={"entity_type": "business_record", "entity_id": rec["id"], "body": "peek"},
        headers=h_other,
    )
    assert other.status_code == 404


# --- per-section AI assist (D-BB-4) -------------------------------------------------------------


def test_section_ai_appends_to_one_block_only(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    client.put(
        f"{BB}/canvases/swot",
        json={"blocks": {"strengths": ["Team"], "threats": ["Incumbent"]}, "version": 1},
        headers=h,
    )
    job = _job_of(
        db, client.post(f"{BB}/canvases/swot/ai-fill", json={"block": "strengths"}, headers=h)
    )
    assert job.payload["block"] == "strengths"
    handle_canvas_ai_fill(db, job)
    canvas = get_or_create_canvas(db, s, canvas_type_of("swot"))
    db.refresh(canvas)
    assert canvas.blocks["strengths"][0] == "Team"  # kept, never rewritten
    assert len(canvas.blocks["strengths"]) == 2  # one stub item appended
    assert canvas.blocks["threats"] == ["Incumbent"]  # other blocks untouched
    assert canvas.blocks["weaknesses"] == []  # even empty ones (section scope)
    assert job.result["status"] == "ready" and job.result["block"] == "strengths"
    assert job.result["added"] == canvas.blocks["strengths"][1:]
    assert list_versions(db, canvas)[0].source == "ai"


def test_section_ai_dedupes_and_reports_nothing_new(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    client.put(
        f"{BB}/canvases/swot",
        json={"blocks": {"strengths": ["[STUB-LLM] items"]}, "version": 1},
        headers=h,
    )
    job = _job_of(
        db, client.post(f"{BB}/canvases/swot/ai-fill", json={"block": "strengths"}, headers=h)
    )
    handle_canvas_ai_fill(db, job)
    assert job.result == {"status": "nothing_new", "block": "strengths", "added": []}


def test_section_ai_on_a_filled_text_block_does_not_overwrite(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/mission_vision", headers=h)
    client.put(
        f"{BB}/canvases/mission_vision",
        json={"blocks": {"mission": "Ours"}, "version": 1},
        headers=h,
    )
    job = _job_of(
        db,
        client.post(f"{BB}/canvases/mission_vision/ai-fill", json={"block": "mission"}, headers=h),
    )
    handle_canvas_ai_fill(db, job)
    canvas = get_or_create_canvas(db, s, canvas_type_of("mission_vision"))
    db.refresh(canvas)
    assert canvas.blocks["mission"] == "Ours"
    assert job.result["status"] == "nothing_new"


def test_section_ai_unknown_block_422_and_whole_canvas_still_works(client, db):
    _s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    bad = client.post(f"{BB}/canvases/swot/ai-fill", json={"block": "nope"}, headers=h)
    assert bad.status_code == 422
    whole = client.post(f"{BB}/canvases/swot/ai-fill", headers=h)
    assert whole.status_code == 202
    assert "block" not in _job_of(db, whole).payload
    assert (
        client.post(
            f"{BB}/canvases/swot/ai-fill", json={"block": "strengths"}, headers=h_bc
        ).status_code
        == 403
    )


# --- Mission & Vision "Give me 3 options" -------------------------------------------------------


def test_mission_vision_options_returns_variants_without_writing(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(_f, h)] = _workspace(db)
    r = client.post(f"{BB}/canvases/mission_vision/options", json={"field": "vision"}, headers=h)
    job = _job_of(db, r)
    assert job.type == "ai.business.mission_vision_options"
    handle_mission_vision_options(db, job)
    assert job.result["status"] == "ready" and job.result["field"] == "vision"
    assert 1 <= len(job.result["options"]) <= 3
    canvas = get_or_create_canvas(db, s, canvas_type_of("mission_vision"))
    assert canvas.blocks["vision"] == ""  # suggestions only; "Use this" is a normal PUT


def test_mission_vision_options_validates_field_and_role(client, db):
    _s, [(_f, h), (_m, h_mentor)] = _workspace(db, MembershipRole.mentor)
    assert (
        client.post(
            f"{BB}/canvases/mission_vision/options", json={"field": "values"}, headers=h
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{BB}/canvases/mission_vision/options", json={"field": "mission"}, headers=h_mentor
        ).status_code
        == 403
    )


# --- personas from validation notes -------------------------------------------------------------


def _interview(db, s, **kw):
    row = Interview(
        startup_id=s.id,
        interviewee=kw.get("interviewee", "Private Person"),
        segment=kw.get("segment", "Market traders"),
        held_on=date(2026, 10, 1),
        notes=kw.get("notes", "Struggles to reconcile cash sales at the end of the day."),
        key_quotes=kw.get("key_quotes", ["I lose track of who owes me"]),
        verdict=InterviewVerdict.supports,
    )
    db.add(row)
    db.flush()
    return row


def test_personas_from_validation_needs_validation_data(client, db):
    _s, [(_f, h)] = _workspace(db)
    r = client.post(f"{BB}/personas/from-validation", headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "NO_VALIDATION_DATA"


def test_personas_from_validation_appends_personas(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(_f, h)] = _workspace(db)
    client.post(f"{BB}/personas", json={"data": {"name": "Existing"}}, headers=h)
    _interview(db, s)
    job = _job_of(db, client.post(f"{BB}/personas/from-validation", headers=h))
    assert job.type == "ai.business.personas_from_validation"
    handle_personas_from_validation(db, job)
    rows = db.query(BusinessRecord).filter_by(startup_id=s.id, kind=RecordKind.persona).all()
    assert len(rows) == 2  # appended next to the founder's own persona, never replacing it
    assert job.result["status"] == "ready"
    assert len(job.result["created_record_ids"]) == 1
    assert job.result["interviews_used"] == 1


def test_validation_prompt_has_no_interviewee_names(db):
    from app.services.business.ai_assist import build_persona_messages, validation_context

    s = create_startup(db, owner=create_user(db))
    _interview(db, s, interviewee="Chiamaka Obi")
    ctx = validation_context(db, s)
    text = " ".join(m.content for m in build_persona_messages(s, ctx))
    assert "Chiamaka" not in text
    assert "reconcile cash sales" in text


# --- pricing advisor ----------------------------------------------------------------------------


def test_pricing_advisor_uses_competitor_prices_and_returns_advice(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(_f, h)] = _workspace(db)
    client.post(
        f"{BB}/competitors", json={"data": {"name": "Paystack", "price": "1.5% per txn"}}, headers=h
    )
    r = client.post(
        f"{BB}/pricing/advisor",
        json={
            "competitor_prices": [{"name": "Moniepoint", "price": "NGN 0 setup"}],
            "willingness_to_pay": "Traders said they'd pay NGN 2,000 a month",
        },
        headers=h,
    )
    job = _job_of(db, r)
    assert job.type == "ai.business.pricing_advisor"
    assert {"name": "Paystack", "price": "1.5% per txn"} in job.payload["competitor_prices"]
    handle_pricing_advisor(db, job)
    advice = job.result["advice"]
    assert job.result["status"] == "ready"
    assert advice["recommended_model"] in {
        "subscription",
        "one_time",
        "usage",
        "freemium",
        "tiered",
    }
    assert advice["wtp_questions"] and advice["tiers"]
    # Advice only: nothing is written until the founder applies it.
    assert db.query(BusinessRecord).filter_by(startup_id=s.id, kind=RecordKind.pricing).count() == 0


def test_pricing_advisor_bounds_inputs(client, db):
    _s, [(_f, h)] = _workspace(db)
    many = [{"name": f"c{i}", "price": "1"} for i in range(21)]
    assert (
        client.post(
            f"{BB}/pricing/advisor", json={"competitor_prices": many}, headers=h
        ).status_code
        == 422
    )
