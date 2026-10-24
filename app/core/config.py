from pydantic import EmailStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Project Info
    PROJECT_NAME: str = "cofoundaz-api"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Server
    SERVER_HOST: str = "http://localhost"
    SERVER_PORT: int = 8000

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"

    # Auth / sessions
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_COOKIE_NAME: str = "cfz_refresh"
    REFRESH_COOKIE_SECURE: bool = True
    REFRESH_COOKIE_SAMESITE: str = "lax"
    LOGIN_MAX_FAILS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15
    MFA_ENCRYPTION_KEY: str | None = None  # 32-byte urlsafe base64 (Fernet)
    # Master key the per-workspace journal keys are derived from (HKDF). Optional like
    # MFA_ENCRYPTION_KEY so the app still boots without it; the journal service raises
    # JournalNotConfigured at use time rather than failing startup for every deployment.
    JOURNAL_ENCRYPTION_KEY: str | None = None  # 32-byte urlsafe base64 (Fernet)
    # Encrypts founders' payment-provider secrets at rest. Optional so the app boots without
    # it; connect/replace/webhook/sync raise FinanceConnectionsNotConfigured at use time.
    FINANCE_CONNECTIONS_ENCRYPTION_KEY: str | None = None  # 32-byte urlsafe base64 (Fernet)
    # Public base URL of this API, used to build per-connection webhook URLs.
    API_PUBLIC_URL: str = ""
    # Dev/e2e only: swap the real Paystack/Flutterwave HTTP calls for an in-process stub.
    PAYMENT_PROVIDER_STUB: bool = False
    # Mono bank link (Finance integrations). The secret key and webhook secret belong to the
    # Cofoundaz Mono app, not to a founder: only the linked account id is per-founder (and is
    # encrypted with FINANCE_CONNECTIONS_ENCRYPTION_KEY). Sandbox only unless MONO_ALLOW_LIVE.
    MONO_SECRET_KEY: str | None = None
    MONO_PUBLIC_KEY: str | None = None
    MONO_WEBHOOK_SECRET: str | None = None
    MONO_ALLOW_LIVE: bool = False
    FINANCE_SYNC_BACKFILL_DAYS: int = 90
    FINANCE_SYNC_MAX_PAGES: int = 20
    FINANCE_SYNC_MIN_INTERVAL_SECONDS: int = 30

    # Providers
    EMAIL_BACKEND: str = "console"  # console | smtp | file
    EMAIL_FILE_DIR: str = "./var/mail"  # where FileEmailSender writes captured emails (dev/e2e)
    STORAGE_BACKEND: str = "local"  # local
    LOCAL_STORAGE_DIR: str = "./var/storage"

    # Cloudinary (only used when STORAGE_BACKEND == "cloudinary"; empty for local/CI).
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Background worker (Module 20 Slice 2)
    WORKER_POLL_INTERVAL: float = 2.0
    WORKER_BATCH_SIZE: int = 10
    WORKER_MAX_ATTEMPTS: int = 5
    WORKER_STALE_SECONDS: int = 300
    APP_BASE_URL: str = ""  # frontend origin for email deep links; falls back to SERVER_HOST
    # Academy certificates: the Cofoundaz LinkedIn page id. Optional; unset, the add-to-profile
    # link names the organisation (organizationName) instead of linking the page.
    ACADEMY_LINKEDIN_ORG_ID: str = ""

    # Scheduler (Module 20 Slice 3)
    SCHEDULER_TIMEZONE: str = "UTC"  # IANA name for the 06:00 check
    MISSION_GEN_HOUR: int = 6  # local hour to pre-generate today's mission
    HEALTH_NIGHTLY_HOUR: int = 3  # local hour for the nightly Health Score pass (PRD 06)
    KPI_SNAPSHOT_HOUR: int = 2  # local hour for the nightly kpi_snapshots capture (PRD 02.1)
    SCHEDULER_INTERVAL: int = 60  # seconds; throttle the scheduler within the worker poll
    QUARTERLY_REASSESS_DAYS: int = 90

    # Logging
    # Path for the loguru file sink. Set to "" to log to stderr only, which is
    # what containerised deployments want (Docker's json-file driver then owns
    # collection and rotation). Default preserves the original local behaviour.
    LOG_FILE_PATH: str = "logs/app.log"

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 120

    # CORS
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> list[str] | str:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, list | str):
            return v
        raise ValueError(v)

    @field_validator("SALES_OUTREACH_SMTP_HOST", "SALES_OUTREACH_IMAP_HOST")
    @classmethod
    def normalize_outreach_host(cls, v: str) -> str:
        """Accept `mail.example.com`, `smtps://mail.example.com/` or padded values; keep the bare
        host. Anything that still is not a hostname fails at boot with a clear message."""
        host = v.strip()
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.rstrip("/")
        if host and not all(c.isalnum() or c in ".-" for c in host):
            raise ValueError(
                f"SALES_OUTREACH_*_HOST must be a bare hostname such as mail.example.com (got {v!r})"
            )
        return host

    @model_validator(mode="after")
    def require_unsubscribe_key_in_production(self) -> "Settings":
        """Unsubscribe links live in recipients' inboxes for years. Signing them with SECRET_KEY
        alone would break every one of them the day SECRET_KEY rotates, so a production
        deployment that sends outreach must set its own key (dev falls back to SECRET_KEY)."""
        if (
            self.ENVIRONMENT == "production"
            and self.SALES_OUTREACH_PROVIDER.strip()
            and not self.SALES_OUTREACH_UNSUBSCRIBE_KEY.strip()
        ):
            raise ValueError(
                "SALES_OUTREACH_UNSUBSCRIBE_KEY must be set in production when "
                "SALES_OUTREACH_PROVIDER is set"
            )
        return self

    @field_validator("SALES_OUTREACH_SMTP_SECURITY")
    @classmethod
    def validate_outreach_security(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"ssl", "starttls", "none"}:
            raise ValueError(
                "SALES_OUTREACH_SMTP_SECURITY must be one of 'ssl', 'starttls', 'none'"
            )
        return v

    @field_validator("REFRESH_COOKIE_SAMESITE")
    @classmethod
    def validate_refresh_cookie_samesite(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError(
                f"REFRESH_COOKIE_SAMESITE must be one of 'lax', 'strict', 'none' (got {v!r})"
            )
        return normalized

    # Database
    DATABASE_URL: str
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    TEST_DATABASE_URL: str | None = None  # e.g. postgresql://.../cofoundaz_test

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Realtime (SSE) delivery (Module 20 Slice 4) -----------------------
    SSE_TICKET_TTL: int = 30  # seconds a one-time stream ticket is valid
    SSE_HEARTBEAT_INTERVAL: int = 20  # seconds between SSE heartbeat comments

    # Email (optional)
    SMTP_TLS: bool = True
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None
    RESEND_API_KEY: str | None = None  # used only when EMAIL_BACKEND == "resend"

    # --- Sales outreach mailbox (D-SAL-1, slice B) ---------------------------
    # A cPanel mailbox, deliberately NOT Resend (Resend carries the app's auth email, so outreach
    # reputation must never touch it). SMTP sends, IMAP reads bounce notices. Unset = sequences
    # save and enroll but nothing sends (the API reports "not configured", never a 500).
    SALES_OUTREACH_PROVIDER: str = ""  # smtp | console (console logs only, never in production)
    SALES_OUTREACH_SMTP_HOST: str = ""
    SALES_OUTREACH_SMTP_PORT: int = 465
    SALES_OUTREACH_SMTP_SECURITY: str = "ssl"  # ssl (465) | starttls (587) | none (local/e2e only)
    SALES_OUTREACH_SMTP_USERNAME: str = ""
    SALES_OUTREACH_SMTP_PASSWORD: str = ""
    SALES_OUTREACH_FROM_EMAIL: str = ""
    SALES_OUTREACH_IMAP_HOST: str = ""
    SALES_OUTREACH_IMAP_PORT: int = 993  # 993 = TLS from the start; anything else uses STARTTLS
    SALES_OUTREACH_IMAP_PROCESSED_FOLDER: str = "Cofoundaz-Processed"
    # Global cap for the one shared mailbox, across every workspace.
    SALES_OUTREACH_HOURLY_LIMIT: int = 30
    # Signs the public unsubscribe links. Falls back to SECRET_KEY; set it so rotating SECRET_KEY
    # cannot break links already sitting in recipients' inboxes.
    SALES_OUTREACH_UNSUBSCRIBE_KEY: str = ""

    # --- LLM seam (Module 03) ---------------------------------------------
    LLM_PROVIDER: str = "openai"  # openai | stub
    LLM_API_KEY: str = ""  # required when LLM_PROVIDER == "openai"
    LLM_MODEL: str = "gpt-5.6-luna"  # OpenAI model id
    # Full API base INCLUDING /v1 (OpenAI SDK convention), e.g. https://api.openai.com/v1.
    # Blank -> OpenAI default (https://api.openai.com/v1); set for Azure/gateway/self-hosted.
    LLM_BASE_URL: str = ""
    LLM_TIMEOUT: int = 60  # seconds per LLM HTTP call (LLM latency >> a normal request)
    LLM_MAX_TOKENS: int = 800  # default output cap
    LLM_DAILY_TOKEN_BUDGET: int = 100000  # per-startup per-UTC-day token cap; <= 0 = unlimited

    # Admin
    FIRST_SUPERUSER_EMAIL: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")


# mypy flags this as missing required args (SECRET_KEY, DATABASE_URL, etc.) because
# pydantic-settings resolves them from the environment/.env at runtime, not from the
# constructor call site — a known false positive with disallow_untyped_defs strictness.
settings = Settings()  # type: ignore[call-arg]
