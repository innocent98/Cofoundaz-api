import enum


class UserStatus(str, enum.Enum):
    pending_verification = "pending_verification"
    active = "active"
    locked = "locked"
    disabled = "disabled"


class MfaType(str, enum.Enum):
    none = "none"
    totp = "totp"
    sms = "sms"


class BusinessModel(str, enum.Enum):
    b2b = "b2b"
    b2c = "b2c"
    b2b2c = "b2b2c"
    marketplace = "marketplace"
    hardware = "hardware"
    services = "services"


class StartupStage(str, enum.Enum):
    idea = "idea"
    validation = "validation"
    build = "build"
    launch = "launch"
    growth = "growth"
    scale = "scale"


class MembershipRole(str, enum.Enum):
    founder = "founder"
    team_member = "team_member"
    mentor = "mentor"
    accountant = "accountant"
    legal_advisor = "legal_advisor"
    business_consultant = "business_consultant"
    investor = "investor"


class MembershipStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    removed = "removed"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class AuthTokenPurpose(str, enum.Enum):
    email_verification = "email_verification"
    # nosec B105 - an enum member naming a token PURPOSE, not a password value.
    password_reset = "password_reset"  # nosec B105


class OAuthProvider(str, enum.Enum):
    google = "google"
    apple = "apple"


class InvitationStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    expired = "expired"
    revoked = "revoked"


class AssessmentType(str, enum.Enum):
    initial = "initial"
    quarterly = "quarterly"


class AssessmentStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"


class Dimension(str, enum.Enum):
    product = "product"
    market = "market"
    money = "money"
    legal = "legal"
    team = "team"


class RecommendationEffort(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RecommendationStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    dismissed = "dismissed"


class RoadmapStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class TaskEffort(str, enum.Enum):
    small = "small"
    medium = "medium"
    large = "large"
