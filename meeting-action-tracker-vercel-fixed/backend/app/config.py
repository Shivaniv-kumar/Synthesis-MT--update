from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://user:pass@localhost:5432/meeting_tracker",
        alias="DATABASE_URL",
    )
    direct_database_url: Optional[str] = Field(default=None, alias="DIRECT_DATABASE_URL")

    @property
    def effective_database_url(self) -> str:
        return self.direct_database_url or self.database_url

    # -------------------------------------------------------------------------
    # Anthropic / LLM
    # -------------------------------------------------------------------------
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    extraction_model: str = Field(default="claude-sonnet-4-6", alias="EXTRACTION_MODEL")
    haiku_model: str = Field(default="claude-haiku-4-5", alias="HAIKU_MODEL")
    opus_model: str = Field(default="claude-opus-4-8", alias="OPUS_MODEL")

    # OpenAI (used by Whisper transcription provider and other optional features)
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # -------------------------------------------------------------------------
    # Auth — JWT
    # -------------------------------------------------------------------------
    secret_key: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # -------------------------------------------------------------------------
    # Auth — TOTP encryption
    # -------------------------------------------------------------------------
    totp_encryption_key: str = Field(
        default="",
        alias="TOTP_ENCRYPTION_KEY",
        description=(
            "Fernet key for encrypting TOTP secrets at rest. "
            "Generate with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\". "
            "Leave empty in development; required in production."
        ),
    )

    # -------------------------------------------------------------------------
    # SSO / Enterprise Identity
    # -------------------------------------------------------------------------
    sso_provider: str = Field(
        default="workos",
        alias="SSO_PROVIDER",
        description="Which SSO provider to use: 'workos' or 'auth0'.",
    )

    # WorkOS
    workos_api_key: str = Field(default="", alias="WORKOS_API_KEY")
    workos_client_id: str = Field(default="", alias="WORKOS_CLIENT_ID")

    # Auth0
    auth0_domain: str = Field(default="", alias="AUTH0_DOMAIN")
    auth0_client_id: str = Field(default="", alias="AUTH0_CLIENT_ID")
    auth0_client_secret: str = Field(default="", alias="AUTH0_CLIENT_SECRET")

    # -------------------------------------------------------------------------
    # Redis / Celery
    # -------------------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # -------------------------------------------------------------------------
    # S3 / Object storage
    # -------------------------------------------------------------------------
    s3_bucket: str = Field(default="meeting-tracker-audio", alias="S3_BUCKET")
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    max_upload_size_mb: int = Field(
        default=500,
        alias="MAX_UPLOAD_SIZE_MB",
        description="Maximum audio/video upload size in megabytes.",
    )

    # -------------------------------------------------------------------------
    # Email / Notifications
    # -------------------------------------------------------------------------
    email_from: str = Field(
        default="noreply@meetingtracker.app",
        alias="EMAIL_FROM",
        description="From address used for all outbound notification emails.",
    )
    sendgrid_api_key: str = Field(
        default="",
        alias="SENDGRID_API_KEY",
        description="SendGrid API key; leave empty to use SMTP instead.",
    )
    email_host: str = Field(
        default="localhost",
        alias="EMAIL_HOST",
        description="SMTP host (used when SENDGRID_API_KEY is not set).",
    )
    email_port: int = Field(
        default=587,
        alias="EMAIL_PORT",
        description="SMTP port.",
    )
    email_user: str = Field(default="", alias="EMAIL_USER")
    email_password: str = Field(default="", alias="EMAIL_PASSWORD")

    # -------------------------------------------------------------------------
    # Transcription
    # -------------------------------------------------------------------------
    transcription_provider: str = Field(
        default="whisper",
        alias="TRANSCRIPTION_PROVIDER",
        description="Transcription backend: 'whisper', 'deepgram', or 'assemblyai'.",
    )
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY")
    assemblyai_api_key: str = Field(default="", alias="ASSEMBLYAI_API_KEY")

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    environment: Literal["development", "staging", "production"] = Field(
        default="development", alias="ENVIRONMENT"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="LOG_LEVEL"
    )
    otlp_endpoint: str = Field(
        default="http://localhost:4317",
        alias="OTLP_ENDPOINT",
        description="OpenTelemetry collector gRPC endpoint for traces and metrics.",
    )

    # -------------------------------------------------------------------------
    # BI / Analytics feed
    # -------------------------------------------------------------------------
    bi_api_key: str = Field(
        default="",
        alias="BI_API_KEY",
        description="Shared secret used to authenticate BI feed consumers.",
    )
    bi_tenant_id: str = Field(
        default="",
        alias="BI_TENANT_ID",
        description="Tenant identifier sent to the BI system for data partitioning.",
    )

    # -------------------------------------------------------------------------
    # CORS / Frontend
    # -------------------------------------------------------------------------
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")

    # -------------------------------------------------------------------------
    # Application metadata
    # -------------------------------------------------------------------------
    # L2: version is configurable instead of hardcoded so CI can stamp it
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")
    # L3: API prefix is configurable
    api_prefix: str = Field(default="/api", alias="API_PREFIX")

    # -------------------------------------------------------------------------
    # Spend cap
    # -------------------------------------------------------------------------
    # M8: per-tenant daily LLM spend cap in USD. When None, the cap is disabled.
    # Set via SPEND_CAP_USD env var (e.g. "5.0" for $5/day/tenant).
    spend_cap_usd: Optional[float] = Field(default=None, alias="SPEND_CAP_USD")

    # -------------------------------------------------------------------------
    # Self-hosted / bootstrap flags
    # -------------------------------------------------------------------------
    # Set ALLOW_PASSWORD_LOGIN=true to enable email+password login even when
    # ENVIRONMENT=production (for self-hosted deployments without SSO).
    allow_password_login: bool = Field(default=False, alias="ALLOW_PASSWORD_LOGIN")

    # Set SEED_ADMIN=true to create or refresh a bootstrap administrator.
    # Configure the credentials below through deployment environment variables.
    seed_admin: bool = Field(default=False, alias="SEED_ADMIN")
    seed_admin_email: str = Field(default="admin@example.com", alias="SEED_ADMIN_EMAIL")
    seed_admin_password: str = Field(default="", alias="SEED_ADMIN_PASSWORD")
    seed_admin_display_name: str = Field(default="Test Administrator", alias="SEED_ADMIN_DISPLAY_NAME")

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_production_settings(self) -> "Settings":
        if self.seed_admin and len(self.seed_admin_password) < 8:
            raise ValueError(
                "SEED_ADMIN_PASSWORD must contain at least 8 characters when "
                "SEED_ADMIN=true."
            )

        if self.environment != "development":
            self.frontend_url = _normalise_https_origin(self.frontend_url)

        if self.environment != "development":
            # C10: Reject the weak default JWT secret
            if self.secret_key == "change-me-in-production":
                raise ValueError(
                    "SECRET_KEY must be changed from the default value in "
                    "staging and production environments. "
                    "Set the SECRET_KEY environment variable to a strong random string."
                )
            # M9: Reject localhost CORS origin in production — CORS would silently
            # block all browser requests if FRONTEND_URL is not set correctly
            if self.frontend_url == "http://localhost:5173":
                raise ValueError(
                    "FRONTEND_URL must be set to the production frontend URL "
                    "in non-development environments (currently 'http://localhost:5173')."
                )
            if _is_supabase_transaction_pooler(self.effective_database_url):
                raise ValueError(
                    "The effective database URL points to the Supabase transaction pooler "
                    "(pooler.supabase.com:6543), which is not compatible with this "
                    "long-running SQLAlchemy asyncpg backend. Use the Supabase "
                    "Session pooler on port 5432 for DATABASE_URL, or a Direct "
                    "connection if your host supports it. Keep DIRECT_DATABASE_URL "
                    "for Alembic migrations when available."
                )
        return self


def _normalise_https_origin(frontend_url: str) -> str:
    frontend_url = frontend_url.strip().rstrip("/")
    if "://" not in frontend_url:
        frontend_url = f"https://{frontend_url}"

    parsed = urlparse(frontend_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "FRONTEND_URL must be a full browser origin, for example "
            "'https://frontend-production.up.railway.app'."
        )
    return frontend_url


def _is_supabase_transaction_pooler(database_url: str) -> bool:
    parsed = urlparse(database_url)
    hostname = parsed.hostname or ""
    return hostname.endswith("pooler.supabase.com") and parsed.port == 6543


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
