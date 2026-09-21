import enum


class UserStatus(enum.StrEnum):
    pending_verification = "pending_verification"
    active = "active"
    locked = "locked"
    disabled = "disabled"


class MfaType(enum.StrEnum):
    none = "none"
    totp = "totp"
    sms = "sms"


class BusinessModel(enum.StrEnum):
    b2b = "b2b"
    b2c = "b2c"
    b2b2c = "b2b2c"
    marketplace = "marketplace"
    hardware = "hardware"
    services = "services"


class StartupStage(enum.StrEnum):
    idea = "idea"
    validation = "validation"
    build = "build"
    launch = "launch"
    growth = "growth"
    scale = "scale"


class MembershipRole(enum.StrEnum):
    founder = "founder"
    team_member = "team_member"
    mentor = "mentor"
    accountant = "accountant"
    legal_advisor = "legal_advisor"
    business_consultant = "business_consultant"
    investor = "investor"


class MembershipStatus(enum.StrEnum):
    active = "active"
    suspended = "suspended"
    removed = "removed"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class AuthTokenPurpose(enum.StrEnum):
    email_verification = "email_verification"
    # nosec B105 - an enum member naming a token PURPOSE, not a password value.
    password_reset = "password_reset"  # nosec B105


class OAuthProvider(enum.StrEnum):
    google = "google"
    apple = "apple"


class InvitationStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    expired = "expired"
    revoked = "revoked"


class AssessmentType(enum.StrEnum):
    initial = "initial"
    quarterly = "quarterly"


class AssessmentStatus(enum.StrEnum):
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"


class Dimension(enum.StrEnum):
    product = "product"
    market = "market"
    money = "money"
    legal = "legal"
    team = "team"


class RecommendationEffort(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class RecommendationStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    dismissed = "dismissed"


class RoadmapStatus(enum.StrEnum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class TaskEffort(enum.StrEnum):
    small = "small"
    medium = "medium"
    large = "large"


class MissionStatus(enum.StrEnum):
    pending = "pending"
    complete = "complete"


class MissionTaskStatus(enum.StrEnum):
    todo = "todo"
    done = "done"
    snoozed = "snoozed"
    rejected = "rejected"


class CanvasType(enum.StrEnum):
    business_model = "business_model"
    lean = "lean"
    value_prop = "value_prop"
    mission_vision = "mission_vision"
    swot = "swot"


class RecordKind(enum.StrEnum):
    persona = "persona"
    revenue_stream = "revenue_stream"
    competitor = "competitor"
    pricing = "pricing"


class ThreatLevel(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class PricingModelType(enum.StrEnum):
    subscription = "subscription"
    one_time = "one_time"
    usage = "usage"
    freemium = "freemium"
    tiered = "tiered"


class SuggestionOp(enum.StrEnum):
    canvas_update = "canvas_update"
    record_create = "record_create"
    record_update = "record_update"
    record_delete = "record_delete"


class SuggestionStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class JournalMood(enum.StrEnum):
    rough = "rough"
    meh = "meh"
    okay = "okay"
    good = "good"
    great = "great"


class DocumentKind(enum.StrEnum):
    business_plan = "business_plan"
    pitch_deck = "pitch_deck"
    financial_model = "financial_model"
    meeting_notes = "meeting_notes"
    one_pager = "one_pager"
    custom = "custom"


class DocumentStatus(enum.StrEnum):
    draft = "draft"
    final = "final"


class ShareAccess(enum.StrEnum):
    view = "view"
    comment = "comment"


class SignatureRequestStatus(enum.StrEnum):
    awaiting = "awaiting"
    complete = "complete"
    cancelled = "cancelled"


class CourseLevel(enum.StrEnum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class BusinessPlanStatus(enum.StrEnum):
    generating = "generating"
    complete = "complete"
    failed = "failed"


class BriefingStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"
    failed = "failed"
