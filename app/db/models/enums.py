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
    password_reset = "password_reset"


class OAuthProvider(str, enum.Enum):
    google = "google"
    apple = "apple"
