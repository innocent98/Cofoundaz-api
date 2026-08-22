"""Live Roadmap journey: a founder onboards to stage "validation" (which
auto-generates the roadmap inline, from `app/services/onboarding/complete.py`
calling `generate_roadmap` synchronously), walks the full tree + phase/
milestone/task CRUD + status-driven progress recompute, then proves the role
boundary (team_member can edit, mentor can read but not write) and the
cross-tenant 404 guard on phase/milestone lookups. It then covers Slice-2:
task dependencies (create, cycle rejection, the whole-roadmap graph, and
`depends_on` on the tree) and the template gallery (list, preview, apply,
idempotent re-apply).

Every response body along the way is captured to `e2e/_captures/roadmap/*.json`
-- those files are the verbatim source for `docs/fe-integration-guide-roadmap.md`
(Task 11).
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard. Does NOT call /complete -- invites (Step 6/7
    below) must be sent while onboarding is still a draft: `onboarding/invites`
    409s with ONBOARDING_ALREADY_COMPLETE once `/complete` has run (see
    `app/services/onboarding/workspace.py::ensure_draft`)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def _wh(c: httpx.Client, auth: dict) -> dict:
    """Onboard just enough to have an active workspace, then build the
    X-Workspace-Id header the way `/auth/me` reports it (same pattern as
    e2e/test_health_score.py::_wh)."""
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _signup_verify_login(c: httpx.Client, mailbox, email: str, password: str) -> dict:
    c.post("/api/v1/auth/signup", json={"email": email, "password": password})
    token = mailbox.latest_token_for(email, subject_contains="Verify")
    c.post("/api/v1/auth/verify", json={"token": token})
    access = c.post("/api/v1/auth/login", json={"email": email, "password": password}).json()[
        "data"
    ]["access_token"]
    return _auth_header(access)


def test_roadmap_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")

        # Invite a team_member + a mentor *before* completing onboarding (see
        # `_onboard_steps` docstring for why order matters here).
        mate_email = unique_email("mate")
        mentor_email = unique_email("mentor")
        inv = c.post(
            "/api/v1/onboarding/invites",
            headers=auth,
            json={
                "invites": [
                    {"email": mate_email, "role": "team_member"},
                    {"email": mentor_email, "role": "mentor"},
                ]
            },
        )
        assert inv.status_code == 200, inv.text
        assert set(inv.json()["data"]["created"]) == {mate_email, mentor_email}
        mate_token = mailbox.latest_token_for(mate_email, subject_contains="invited")
        mentor_token = mailbox.latest_token_for(mentor_email, subject_contains="invited")

        # Teammate signs up (email MUST match the invite -- acceptance is
        # email-bound) + verifies + accepts. One shared client is fine here: every
        # call carries an explicit Authorization header rather than relying on
        # cookies (same approach as e2e/test_health_score.py).
        mate_pw = "Mate-" + mate_email.split("@")[0] + "-9"
        mate_auth = _signup_verify_login(c, mailbox, mate_email, mate_pw)
        mate_accept = c.post(
            "/api/v1/invitations/accept", json={"token": mate_token}, headers=mate_auth
        )
        assert mate_accept.status_code == 200, mate_accept.text

        # Mentor signs up + verifies + accepts.
        mentor_pw = "Mentor-" + mentor_email.split("@")[0] + "-9"
        mentor_auth = _signup_verify_login(c, mailbox, mentor_email, mentor_pw)
        mentor_accept = c.post(
            "/api/v1/invitations/accept", json={"token": mentor_token}, headers=mentor_auth
        )
        assert mentor_accept.status_code == 200, mentor_accept.text

        # Founder completes onboarding -- synchronously generates the roadmap
        # (`generate_roadmap` is called inline from `complete_onboarding`, not
        # deferred to the `roadmap.generate` job it also enqueues).
        done = c.post("/api/v1/onboarding/complete", headers=auth)
        assert done.status_code == 200, done.text

        wh = _wh(c, auth)
        mate_wh = _wh(c, mate_auth)
        mentor_wh = _wh(c, mentor_auth)

        # 1. GET roadmap -- already built, stage "validation" template.
        tree = c.get("/api/v1/roadmap", headers=wh)
        assert tree.status_code == 200, tree.text
        tree_data = tree.json()["data"]
        assert tree_data["roadmap"]["template_key"] == "stage.validation"
        assert tree_data["current_stage"] == "validation"
        assert len(tree_data["phases"]) >= 1
        first_phase = tree_data["phases"][0]
        assert first_phase["milestones"], "expected at least one milestone in phase 1"
        assert first_phase["milestones"][0]["tasks"], "expected at least one task in milestone 1"
        capture("roadmap", "get_tree", tree)

        # 2. POST /generate -- roadmap already exists, so this is the
        # already-claimed idempotent path (unique index on startup_id).
        gen = c.post("/api/v1/roadmap/generate", headers=wh)
        assert gen.status_code == 202, gen.text
        assert gen.json()["data"]["status"] == "succeeded"
        capture("roadmap", "generate", gen)

        # 3. Create a phase, a milestone under it, a task under that milestone.
        phase = c.post("/api/v1/roadmap/phases", headers=wh, json={"name": "Custom phase"})
        assert phase.status_code == 201, phase.text
        phase_id = phase.json()["data"]["id"]
        capture("roadmap", "phase_create", phase)

        milestone = c.post(
            "/api/v1/roadmap/milestones",
            headers=wh,
            json={"phase_id": phase_id, "title": "Custom milestone"},
        )
        assert milestone.status_code == 201, milestone.text
        milestone_id = milestone.json()["data"]["id"]
        capture("roadmap", "milestone_create", milestone)

        task = c.post(
            "/api/v1/roadmap/tasks",
            headers=wh,
            json={"milestone_id": milestone_id, "title": "Custom task"},
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["data"]["id"]
        capture("roadmap", "task_create", task)

        # 4. PATCH the task -> done; the milestone's progress (1/1 tasks done)
        # must reflect it on the next GET.
        task_patch = c.patch(
            f"/api/v1/roadmap/tasks/{task_id}", headers=wh, json={"status": "done"}
        )
        assert task_patch.status_code == 200, task_patch.text
        assert task_patch.json()["data"]["status"] == "done"
        capture("roadmap", "task_patch", task_patch)

        after = c.get("/api/v1/roadmap", headers=wh)
        assert after.status_code == 200, after.text
        all_milestones = [m for p in after.json()["data"]["phases"] for m in p["milestones"]]
        our_milestone = next(m for m in all_milestones if m["id"] == milestone_id)
        assert our_milestone["progress"] == 100
        capture("roadmap", "get_tree_after_edits", after)

        # 5. PATCH the milestone itself -> done.
        milestone_patch = c.patch(
            f"/api/v1/roadmap/milestones/{milestone_id}", headers=wh, json={"status": "done"}
        )
        assert milestone_patch.status_code == 200, milestone_patch.text
        assert milestone_patch.json()["data"]["status"] == "done"
        capture("roadmap", "milestone_complete", milestone_patch)

        # 6. Malformed PATCH -- explicit null on a non-nullable field is rejected
        # by TaskUpdate's `_reject_explicit_null` validator (app/schemas/roadmap.py).
        bad = c.patch(f"/api/v1/roadmap/tasks/{task_id}", headers=wh, json={"title": None})
        assert bad.status_code == 422, bad.text
        assert bad.json()["error"]["code"] == "VALIDATION_ERROR"
        capture("roadmap", "validation_error", bad)

        # 7. The team_member can edit -- role is in `_editor` (founder, team_member).
        mate_patch = c.patch(
            f"/api/v1/roadmap/tasks/{task_id}", headers=mate_wh, json={"status": "in_progress"}
        )
        assert mate_patch.status_code == 200, mate_patch.text
        assert mate_patch.json()["data"]["status"] == "in_progress"

        # 8. The mentor can read the whole roadmap...
        mentor_read = c.get("/api/v1/roadmap", headers=mentor_wh)
        assert mentor_read.status_code == 200, mentor_read.text

        # ...but any write is 403 -- mentor is not in `_editor`.
        mentor_write = c.post(
            "/api/v1/roadmap/phases", headers=mentor_wh, json={"name": "Should not be created"}
        )
        assert mentor_write.status_code == 403, mentor_write.text
        assert mentor_write.json()["error"]["code"] == "FORBIDDEN"
        capture("roadmap", "mentor_forbidden", mentor_write)

        # 9. Cross-tenant: a second founder, with their OWN generated roadmap,
        # cannot reach the first founder's phase/milestone by id. This proves the
        # `roadmap_id` join filter in `_phase`/`_milestone` (app/api/v1/endpoints/
        # roadmap.py) actually scopes by tenant -- not merely "no roadmap yet".
        u2 = make_verified_user(c)
        access2 = c.post("/api/v1/auth/login", json=u2).json()["data"]["access_token"]
        auth2 = _auth_header(access2)
        _onboard_steps(c, auth2, stage="idea", name="Second Startup")
        done2 = c.post("/api/v1/onboarding/complete", headers=auth2)
        assert done2.status_code == 200, done2.text
        wh2 = _wh(c, auth2)

        cross_phase = c.patch(
            f"/api/v1/roadmap/phases/{phase_id}", headers=wh2, json={"name": "hijacked"}
        )
        assert cross_phase.status_code == 404, cross_phase.text
        assert cross_phase.json()["error"]["code"] == "NOT_FOUND"
        capture("roadmap", "cross_tenant_404", cross_phase)

        cross_milestone = c.patch(
            f"/api/v1/roadmap/milestones/{milestone_id}", headers=wh2, json={"status": "todo"}
        )
        assert cross_milestone.status_code == 404, cross_milestone.text
        assert cross_milestone.json()["error"]["code"] == "NOT_FOUND"

        # 10. Dependencies -- create two tasks under our (first founder's)
        # milestone, then wire task_a -> depends on -> task_b.
        task_a = c.post(
            "/api/v1/roadmap/tasks",
            headers=wh,
            json={"milestone_id": milestone_id, "title": "Task A"},
        )
        assert task_a.status_code == 201, task_a.text
        task_a_id = task_a.json()["data"]["id"]

        task_b = c.post(
            "/api/v1/roadmap/tasks",
            headers=wh,
            json={"milestone_id": milestone_id, "title": "Task B"},
        )
        assert task_b.status_code == 201, task_b.text
        task_b_id = task_b.json()["data"]["id"]

        dep_create = c.post(
            f"/api/v1/roadmap/tasks/{task_a_id}/dependencies",
            headers=wh,
            json={"depends_on_task_id": task_b_id},
        )
        assert dep_create.status_code == 201, dep_create.text
        assert dep_create.json()["data"] == {
            "task_id": task_a_id,
            "depends_on_task_id": task_b_id,
        }
        capture("roadmap", "dependency_create", dep_create)

        # 11. The reverse edge (B depends on A) would close a loop -- rejected.
        dep_cycle = c.post(
            f"/api/v1/roadmap/tasks/{task_b_id}/dependencies",
            headers=wh,
            json={"depends_on_task_id": task_a_id},
        )
        assert dep_cycle.status_code == 409, dep_cycle.text
        assert dep_cycle.json()["error"]["code"] == "DEPENDENCY_CYCLE"
        capture("roadmap", "dependency_cycle", dep_cycle)

        # 12. GET /roadmap/dependencies -- the whole-roadmap graph.
        graph = c.get("/api/v1/roadmap/dependencies", headers=wh)
        assert graph.status_code == 200, graph.text
        graph_data = graph.json()["data"]
        assert {"task_id": task_a_id, "depends_on_task_id": task_b_id} in graph_data["edges"]
        assert any(n["task_id"] == task_a_id for n in graph_data["nodes"])
        capture("roadmap", "dependencies_graph", graph)

        # 13. Re-GET the tree -- task A's `depends_on` now lists task B (from
        # `serialize_tree`'s dependency_map, not the stubbed task-CRUD output).
        tree_with_deps = c.get("/api/v1/roadmap", headers=wh)
        assert tree_with_deps.status_code == 200, tree_with_deps.text
        all_tasks = [
            t
            for p in tree_with_deps.json()["data"]["phases"]
            for m in p["milestones"]
            for t in m["tasks"]
        ]
        our_task_a = next(t for t in all_tasks if t["id"] == task_a_id)
        assert our_task_a["depends_on"] == [task_b_id]
        capture("roadmap", "get_tree_with_deps", tree_with_deps)

        # 14. Templates gallery -- mvp-build is listed, not yet applied.
        templates_list = c.get("/api/v1/roadmap/templates", headers=wh)
        assert templates_list.status_code == 200, templates_list.text
        mvp_entry = next(
            t for t in templates_list.json()["data"] if t["id"] == "mvp-build"
        )
        assert mvp_entry["applied"] is False
        capture("roadmap", "templates_list", templates_list)

        # 15. Template preview -- full phase/milestone/task breakdown.
        template_preview = c.get("/api/v1/roadmap/templates/mvp-build", headers=wh)
        assert template_preview.status_code == 200, template_preview.text
        assert template_preview.json()["data"]["id"] == "mvp-build"
        capture("roadmap", "template_preview", template_preview)

        # 16. Apply the template -- first application actually adds content.
        template_apply = c.post("/api/v1/roadmap/templates/mvp-build/apply", headers=wh)
        assert template_apply.status_code == 201, template_apply.text
        apply_data = template_apply.json()["data"]
        assert apply_data["already_applied"] is False
        assert apply_data["added"]["phases"] >= 1
        assert apply_data["added"]["milestones"] >= 1
        assert apply_data["added"]["tasks"] >= 1
        capture("roadmap", "template_apply", template_apply)

        # 17. Re-apply -- idempotent no-op, already_applied.
        template_apply_noop = c.post("/api/v1/roadmap/templates/mvp-build/apply", headers=wh)
        assert template_apply_noop.status_code == 200, template_apply_noop.text
        assert template_apply_noop.json()["data"] == {
            "already_applied": True,
            "added": {"phases": 0, "milestones": 0, "tasks": 0},
        }
        capture("roadmap", "template_apply_noop", template_apply_noop)

        # 18. The gallery now reflects the applied state.
        templates_list_after = c.get("/api/v1/roadmap/templates", headers=wh)
        assert templates_list_after.status_code == 200, templates_list_after.text
        mvp_entry_after = next(
            t for t in templates_list_after.json()["data"] if t["id"] == "mvp-build"
        )
        assert mvp_entry_after["applied"] is True
