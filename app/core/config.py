from pydantic import EmailStr, field_validator
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

    # Providers
    EMAIL_BACKEND: str = "console"  # console | smtp | file
    EMAIL_FILE_DIR: str = "./var/mail"  # where FileEmailSender writes captured emails (dev/e2e)
    STORAGE_BACKEND: str = "local"  # local
    LOCAL_STORAGE_DIR: str = "./var/storage"

    # Cloudinary (only used when STORAGE_BACKEND == "cloudinary"; empty for local/CI).
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

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

    # Email (optional)
    SMTP_TLS: bool = True
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    # Admin
    FIRST_SUPERUSER_EMAIL: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")


# mypy flags this as missing required args (SECRET_KEY, DATABASE_URL, etc.) because
# pydantic-settings resolves them from the environment/.env at runtime, not from the
# constructor call site — a known false positive with disallow_untyped_defs strictness.
settings = Settings()  # type: ignore[call-arg]
