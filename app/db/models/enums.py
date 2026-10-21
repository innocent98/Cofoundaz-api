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


# Module access sets, single-sourced so the dashboard KPI tiles can't drift from the module
# routes they summarise (an investor must not read revenue off the dashboard when the Finance
# route itself would 403 them).
FINANCE_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant}
)
MARKETING_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member}
)
SALES_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member}
)
# D-MIS-8: Today's Mission reads (and the AI briefing, which is built from the same workspace
# data) are founder + team member only. One definition, shared by routes, search and notifications.
MISSION_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member}
)
BRIEFING_ROLES: frozenset[MembershipRole] = MISSION_ROLES


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


class ChatMessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"


class ChatMessageStatus(enum.StrEnum):
    # user turns are `ready` immediately; an assistant turn starts `generating`
    # (Pattern B: the POST enqueues ai.cofounder.reply; the FE polls) and ends
    # `ready` or `failed` (LLM error / over budget).
    generating = "generating"
    ready = "ready"
    failed = "failed"


class EnrichmentStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"


class ContentStatus(enum.StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    published = "published"


class ChannelStatus(enum.StrEnum):
    active = "active"
    testing = "testing"
    paused = "paused"
    not_started = "not_started"


class ChannelKey(enum.StrEnum):
    organic_social = "organic_social"
    paid_social = "paid_social"
    search = "search"
    email = "email"
    content_seo = "content_seo"
    partnerships = "partnerships"
    events = "events"
    referral = "referral"


class CampaignObjective(enum.StrEnum):
    awareness = "awareness"
    leads = "leads"
    sales = "sales"
    launch = "launch"


class CampaignStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"


class CopyPlatform(enum.StrEnum):
    """Where a piece of copy will run; picks the length limit (see services/marketing/platforms)."""

    x = "x"
    instagram = "instagram"
    linkedin = "linkedin"
    facebook = "facebook"
    whatsapp = "whatsapp"
    google_ads_headline = "google_ads_headline"
    google_ads_description = "google_ads_description"
    email_subject = "email_subject"


class FitLevel(enum.StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class MarketingGenerationKind(enum.StrEnum):
    copy = "copy"
    plan_week = "plan_week"
    channel_plan = "channel_plan"
    channel_fit = "channel_fit"
    content_gap = "content_gap"


class MarketingGenerationStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"
    failed = "failed"


class AssetType(enum.StrEnum):
    ad = "ad"
    social_post = "social_post"
    email = "email"
    landing_headline = "landing_headline"
    product_description = "product_description"


class CopyTone(enum.StrEnum):
    bold = "bold"
    friendly = "friendly"
    expert = "expert"
    playful = "playful"


class RiskLevel(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class AssumptionStatus(enum.StrEnum):
    untested = "untested"
    testing = "testing"
    validated = "validated"
    invalidated = "invalidated"


class ExperimentType(enum.StrEnum):
    smoke_test = "smoke_test"
    landing_page = "landing_page"
    ad_test = "ad_test"
    other = "other"


class ExperimentStatus(enum.StrEnum):
    draft = "draft"
    live = "live"
    ended = "ended"


class InterviewVerdict(enum.StrEnum):
    supports = "supports"
    contradicts = "contradicts"
    neutral = "neutral"


class SurveyStatus(enum.StrEnum):
    draft = "draft"
    open = "open"
    closed = "closed"


class MarketingMetricName(enum.StrEnum):
    visits = "visits"
    impressions = "impressions"
    clicks = "clicks"
    conversions = "conversions"
    spend = "spend"


class TransactionDirection(enum.StrEnum):
    inflow = "in"
    outflow = "out"


class TransactionSource(enum.StrEnum):
    manual = "manual"
    bank = "bank"
    accounting = "accounting"
    paystack = "paystack"
    flutterwave = "flutterwave"
    invoice = "invoice"
    expense = "expense"


class ConnectionStatus(enum.StrEnum):
    active = "active"
    needs_attention = "needs_attention"
    disconnected = "disconnected"


class PaymentProvider(enum.StrEnum):
    paystack = "paystack"
    flutterwave = "flutterwave"


class InvoiceStatus(enum.StrEnum):
    draft = "draft"
    sent = "sent"
    paid = "paid"


class InvoiceTerms(enum.StrEnum):
    net_15 = "net_15"
    net_30 = "net_30"
    due_on_receipt = "due_on_receipt"


class FinancialModelStatus(enum.StrEnum):
    generating = "generating"
    complete = "complete"
    failed = "failed"


class CalendarEventType(enum.StrEnum):
    milestone = "milestone"
    meeting = "meeting"
    deadline = "deadline"
    task = "task"


class LeadSource(enum.StrEnum):
    manual = "manual"
    csv = "csv"
    referral = "referral"
    inbound = "inbound"
    event = "event"
    other = "other"


class LeadStatus(enum.StrEnum):
    new = "new"
    working = "working"
    qualified = "qualified"
    disqualified = "disqualified"
    converted = "converted"


class StageKind(enum.StrEnum):
    open = "open"
    won = "won"
    lost = "lost"


class ActivityKind(enum.StrEnum):
    call = "call"
    email = "email"
    meeting = "meeting"
    note = "note"


class ClosedReason(enum.StrEnum):
    price = "price"
    timing = "timing"
    competitor = "competitor"
    no_budget = "no_budget"
    ghosted = "ghosted"
    other = "other"


class CoachDifficulty(enum.StrEnum):
    friendly = "friendly"
    neutral = "neutral"
    tough = "tough"


class CoachSessionStatus(enum.StrEnum):
    # `scoring` while the scorecard job runs; `ended` once it finished (scorecard present) or
    # failed (scorecard null + scorecard_error set; ending again retries).
    active = "active"
    scoring = "scoring"
    ended = "ended"


class CallPrepStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"
    failed = "failed"
