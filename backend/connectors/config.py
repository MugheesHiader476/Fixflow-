from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from backend.schemas.pipeline import Contract


class ConnectorConfig(Contract):
    public_url: str = "http://localhost:3000"
    vault_key: SecretStr | None = None
    vault_previous_keys: list[SecretStr] = Field(default_factory=list)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    github_api_version: str = "2026-03-10"
    slack_client_id: str | None = None
    slack_client_secret: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    gmail_pubsub_topic: str | None = None
    gmail_pubsub_service_account: str | None = None
    events_enabled: bool = False
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_response_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    page_size: int = Field(default=25, ge=1, le=100)
    reconciliation_seconds: int = Field(default=86400, ge=60, le=86400)
    polling_seconds: int = Field(default=300, ge=30, le=3600)
    acl_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    max_sync_resources: int = Field(default=100000, ge=1, le=1000000)
    max_retries: int = Field(default=3, ge=0, le=10)
    slack_history_interval_seconds: int = Field(default=60, ge=1, le=3600)
    user_actions_per_minute: int = Field(default=30, ge=1, le=1000)

    @field_validator(
        "vault_key",
        "google_client_id",
        "google_client_secret",
        "github_client_id",
        "github_client_secret",
        "github_app_id",
        "github_app_slug",
        "github_private_key",
        "github_webhook_secret",
        "slack_client_id",
        "slack_client_secret",
        "slack_signing_secret",
        "gmail_pubsub_topic",
        "gmail_pubsub_service_account",
        mode="before",
    )
    @classmethod
    def empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("public_url")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
        ):
            raise ValueError("Connector public URL must be an HTTPS origin (localhost HTTP is allowed)")
        return value.rstrip("/")
