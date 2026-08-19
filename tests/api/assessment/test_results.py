import uuid
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus, MembershipRole
from tests.factories import create_assessment, create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _walk(client, h, aid):
    vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    while True:
        nq = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h).json()["data"][
            "next_question"
        ]
        if nq is None:
            break
        if nq["qtype"] == "single_choice":
            val = nq["options"][0]["value"]
        elif nq["qtype"] == "multi_choice":
            val = [nq["options"][0]["value"]]
        else:
            val = vals[nq["qtype"]]
        r = client.post(
            f"/api/v1/assessments/{aid}/answers",
            headers=h,
            json={"question_key": nq["key"], "value": val},
        )
        assert r.status_code == 200, r.text


def _complete_result(db, assessment, *, dimension_scores: dict[str, int] | None = None):
    r = AssessmentResult(
        assessment_id=assessment.id,
        dimension_scores=dimension_scores
        or {"product": 80, "market": 70, "money": 60, "legal": 90, "team": 75},
        overall_provisional=75,
        narrative="Solid across the board.",
    )
    db.add(r)
    db.flush()
    return r


def test_list_and_detail_and_compare(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)
    client.post(f"/api/v1/assessments/{aid}/complete", headers=h)

    lst = client.get("/api/v1/assessments", headers=h)
    assert lst.status_code == 200, lst.text
    data = lst.json()["data"]
    assert len(data) == 1
    assert data[0]["id"] == aid
    assert data[0]["status"] == "completed"
    assert data[0]["overall_provisional"] is not None

    det = client.get(f"/api/v1/assessments/{aid}", headers=h)
    assert det.status_code == 200, det.text
    ddata = det.json()["data"]
    assert ddata["result"]["dimension_scores"]
    # answers are bucketed by dimension, not returned as a flat list
    assert set(ddata["answers_by_dimension"]) == {"product", "market", "money", "legal", "team"}
    assert "unknown" not in ddata["answers_by_dimension"]
    for bucket in ddata["answers_by_dimension"].values():
        for entry in bucket:
            assert set(entry) == {"question_key", "value"}

    cmp = client.get(f"/api/v1/assessments/compare?ids={aid}", headers=h)
    assert cmp.status_code == 200, cmp.text
    cdata = cmp.json()["data"]
    assert len(cdata) == 1
    assert cdata[0]["id"] == aid
    assert cdata[0]["dimension_scores"] == ddata["result"]["dimension_scores"]


def test_team_member_can_read_results(client, db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    tm = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, tm, s, role=MembershipRole.team_member)
    db.commit()
    tm_h = {
        "Authorization": f"Bearer {create_access_token(str(tm.id))}",
        "X-Workspace-Id": str(s.id),
    }
    assert client.get("/api/v1/assessments", headers=tm_h).status_code == 200


def test_list_requires_membership(client, db):
    _u_a, s_a, _h_a = _founder(db)
    outsider = create_user(db, email_verified_at=datetime.now(UTC))
    db.commit()
    h = {
        "Authorization": f"Bearer {create_access_token(str(outsider.id))}",
        "X-Workspace-Id": str(s_a.id),
    }
    assert client.get("/api/v1/assessments", headers=h).status_code == 403


def test_list_excludes_other_workspace(client, db):
    _u_a, s_a, h_a = _founder(db)
    _u_b, s_b, _h_b = _founder(db)
    create_assessment(db, s_b)
    db.commit()

    r = client.get("/api/v1/assessments", headers=h_a)
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_detail_cross_tenant_404(client, db):
    _u_b, s_b, _h_b = _founder(db)
    b_assessment = create_assessment(db, s_b)
    db.commit()

    _u_a, _s_a, h_a = _founder(db)
    db.commit()

    r = client.get(f"/api/v1/assessments/{b_assessment.id}", headers=h_a)
    assert r.status_code == 404


def test_detail_unknown_assessment_404(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.get(f"/api/v1/assessments/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404


def test_compare_rejects_more_than_three_ids(client, db):
    u, s, h = _founder(db)
    db.commit()
    ids = ",".join(str(uuid.uuid4()) for _ in range(4))
    r = client.get(f"/api/v1/assessments/compare?ids={ids}", headers=h)
    assert r.status_code == 422


def test_compare_rejects_malformed_id(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.get("/api/v1/assessments/compare?ids=not-a-uuid", headers=h)
    assert r.status_code == 422


def test_compare_rejects_incomplete_assessment(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    r = client.get(f"/api/v1/assessments/compare?ids={aid}", headers=h)
    assert r.status_code == 404


def test_compare_rejects_cross_tenant_assessment(client, db):
    _u_b, s_b, _h_b = _founder(db)
    b_assessment = create_assessment(db, s_b, status=AssessmentStatus.completed)
    _complete_result(db, b_assessment)
    db.commit()

    _u_a, _s_a, h_a = _founder(db)
    db.commit()

    r = client.get(f"/api/v1/assessments/compare?ids={b_assessment.id}", headers=h_a)
    assert r.status_code == 404


def test_detail_unknown_bank_key_buckets_as_unknown(client, db):
    from tests.factories import create_answer

    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    a = client.get(f"/api/v1/assessments/{aid}", headers=h)  # just to have committed assessment
    assert a.status_code == 200

    from app.db.models.assessment import Assessment

    assessment = db.query(Assessment).filter(Assessment.id == aid).first()
    create_answer(db, assessment, question_key="retired_question_from_v0", value="n/a")
    db.commit()

    det = client.get(f"/api/v1/assessments/{aid}", headers=h)
    assert det.status_code == 200, det.text
    assert "unknown" in det.json()["data"]["answers_by_dimension"]
    assert det.json()["data"]["answers_by_dimension"]["unknown"] == [
        {"question_key": "retired_question_from_v0", "value": "n/a"}
    ]
