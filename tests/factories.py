import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentAnswer
from app.db.models.auth import AuthSession
from app.db.models.enums import (
    AssessmentStatus,
    AssessmentType,
    InvitationStatus,
    MembershipRole,
    MembershipStatus,
    RecommendationEffort,
    RecommendationStatus,
    RoadmapStatus,
    StartupStage,
    TaskEffort,
)
from app.db.models.health_score import HealthRecommendation, HealthScore, HealthScoreHistory
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.roadmap import (
    Roadmap,
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapReplan,
    RoadmapTask,
    RoadmapTaskDependency,
)
from app.db.models.startup import Startup, StartupProfile
from app.db.models.user import User, UserProfile


def create_user(db: Session, *, email: str | None = None, **kw) -> User:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(email=email, **kw)
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    return user


def create_startup(db: Session, *, owner: User, name: str = "Acme", **kw) -> Startup:
    s = Startup(name=name, created_by=owner.id, **kw)
    s.profile = StartupProfile()
    db.add(s)
    db.flush()
    return s


def create_assessment(
    db: Session,
    startup: Startup,
    *,
    creator: User | None = None,
    type: AssessmentType = AssessmentType.initial,
    status: AssessmentStatus = AssessmentStatus.in_progress,
    bank_version: str = "v1",
) -> Assessment:
    a = Assessment(
        startup_id=startup.id,
        type=type,
        status=status,
        bank_version=bank_version,
        created_by=(creator.id if creator else startup.created_by),
    )
    db.add(a)
    db.flush()
    return a


def create_answer(
    db: Session, assessment: Assessment, *, question_key: str, value: object
) -> AssessmentAnswer:
    ans = AssessmentAnswer(assessment_id=assessment.id, question_key=question_key, value_json=value)
    db.add(ans)
    db.flush()
    return ans


def create_membership(
    db: Session, user: User, startup: Startup, role: MembershipRole = MembershipRole.founder
) -> Membership:
    m = Membership(
        user_id=user.id, startup_id=startup.id, role=role, status=MembershipStatus.active
    )
    db.add(m)
    return m


def create_invitation(
    db: Session,
    startup: Startup,
    *,
    email: str = "invitee@example.com",
    role: MembershipRole = MembershipRole.team_member,
    inviter: User | None = None,
    token_hash: str = "invtok",
    status: InvitationStatus = InvitationStatus.pending,
    expires_at: datetime | None = None,
) -> Invitation:
    inv = Invitation(
        startup_id=startup.id,
        email=email,
        role=role,
        token_hash=token_hash,
        status=status,
        invited_by=(inviter.id if inviter else startup.created_by),
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=14),
    )
    db.add(inv)
    db.flush()
    return inv


def create_health_score(
    db: Session,
    startup: Startup,
    *,
    score: int = 70,
    dimension_scores: dict | None = None,
    band: str = "healthy",
    config_version: int = 1,
) -> HealthScore:
    hs = HealthScore(
        startup_id=startup.id,
        score=score,
        band=band,
        dimension_scores=dimension_scores
        or {"product": 70, "market": 70, "money": 70, "legal": 70, "team": 70},
        source="assessment",
        config_version=config_version,
    )
    db.add(hs)
    db.flush()
    return hs


def create_history(
    db: Session,
    startup: Startup,
    *,
    score: int,
    delta: int = 0,
    computed_at: datetime | None = None,
    trigger: str = "test",
    dimension_scores: dict | None = None,
    config_version: int = 1,
) -> HealthScoreHistory:
    h = HealthScoreHistory(
        startup_id=startup.id,
        score=score,
        delta=delta,
        trigger=trigger,
        dimension_scores=dimension_scores or {"money": score},
        config_version=config_version,
    )
    db.add(h)
    db.flush()
    if computed_at is not None:
        h.created_at = computed_at
        db.flush()
    return h


def create_recommendation(
    db: Session,
    startup: Startup,
    *,
    key: str,
    dimension: str = "money",
    status: RecommendationStatus = RecommendationStatus.pending,
    priority: int = 1,
    estimated_lift: int = 8,
    effort: RecommendationEffort = RecommendationEffort.medium,
    title: str = "t",
    body: str = "b",
) -> HealthRecommendation:
    r = HealthRecommendation(
        startup_id=startup.id,
        dimension=dimension,
        key=key,
        title=title,
        body=body,
        estimated_lift=estimated_lift,
        effort=effort,
        status=status,
        priority=priority,
    )
    db.add(r)
    db.flush()
    return r


def create_roadmap(
    db: Session,
    startup: Startup,
    *,
    stage: StartupStage = StartupStage.validation,
    template_key: str = "stage.validation",
    template_version: int = 1,
) -> Roadmap:
    r = Roadmap(
        startup_id=startup.id,
        stage=stage,
        template_key=template_key,
        template_version=template_version,
    )
    db.add(r)
    db.flush()
    return r


def create_phase(
    db: Session, roadmap: Roadmap, *, name: str = "Phase", order: int = 0
) -> RoadmapPhase:
    p = RoadmapPhase(roadmap_id=roadmap.id, name=name, order=order)
    db.add(p)
    db.flush()
    return p


def create_milestone(
    db: Session,
    phase: RoadmapPhase,
    *,
    title: str = "M",
    status: RoadmapStatus = RoadmapStatus.todo,
    progress: int = 0,
    due_on: date | None = None,
    owner: User | None = None,
    order: int = 0,
) -> RoadmapMilestone:
    m = RoadmapMilestone(
        phase_id=phase.id,
        title=title,
        status=status,
        progress=progress,
        due_on=due_on,
        owner_id=(owner.id if owner else None),
        order=order,
    )
    db.add(m)
    db.flush()
    return m


def create_task(
    db: Session,
    milestone: RoadmapMilestone,
    *,
    title: str = "T",
    effort: TaskEffort = TaskEffort.medium,
    status: RoadmapStatus = RoadmapStatus.todo,
    assignee: User | None = None,
    due_on: date | None = None,
    order: int = 0,
) -> RoadmapTask:
    t = RoadmapTask(
        milestone_id=milestone.id,
        title=title,
        effort=effort,
        status=status,
        assignee_id=(assignee.id if assignee else None),
        due_on=due_on,
        order=order,
    )
    db.add(t)
    db.flush()
    return t


def create_dependency(
    db: Session, dependent: RoadmapTask, depends_on: RoadmapTask
) -> RoadmapTaskDependency:
    d = RoadmapTaskDependency(task_id=dependent.id, depends_on_task_id=depends_on.id)
    db.add(d)
    db.flush()
    return d


def create_replan(
    db: Session,
    roadmap: Roadmap,
    *,
    applied_by: User | uuid.UUID | None = None,
    change_count: int = 1,
    changes: list | None = None,
    summary: str = "Re-planned 1 milestone",
) -> RoadmapReplan:
    # `applied_by` is a real FK to `users.id` (NOT NULL) — a raw startup_id
    # would violate the constraint, so create a real user when none is given.
    if applied_by is None:
        applied_by_id = create_user(db).id
    elif isinstance(applied_by, User):
        applied_by_id = applied_by.id
    else:
        applied_by_id = applied_by
    r = RoadmapReplan(
        roadmap_id=roadmap.id,
        applied_by=applied_by_id,
        change_count=change_count,
        changes=changes or [],
        summary=summary,
    )
    db.add(r)
    db.flush()
    return r


def create_auth_session(
    db: Session,
    user: User,
    *,
    refresh_token_hash: str = "hash",
    family_id: uuid.UUID | None = None,
    **kw,
) -> AuthSession:
    s = AuthSession(
        user_id=user.id,
        refresh_token_hash=refresh_token_hash,
        family_id=family_id or uuid.uuid4(),
        expires_at=kw.pop("expires_at", datetime.now(UTC) + timedelta(days=30)),
        **kw,
    )
    db.add(s)
    db.flush()
    return s
