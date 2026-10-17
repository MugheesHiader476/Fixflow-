from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

from backend.connectors.config import ConnectorConfig
from backend.processing.pipeline.config import PipelineConfig
from backend.schemas.agent import GrokConfig
from backend.schemas.evidence import EvidenceConfig, GeminiConfig
from backend.security.config import SecurityConfig

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ROOT / "backend/.env"),
        extra="ignore",
        case_sensitive=False,
        env_nested_delimiter="__",
    )

    fixflow_api_token: SecretStr | None = None
    max_extracted_chars: int = Field(default=5_000_000, ge=1)
    database_url: SecretStr | None = None
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "fixflow"
    postgres_user: str = "fixflow"
    postgres_password: SecretStr | None = None
    frontend_origins: str = "http://localhost:3000"
    fixflow_data_dir: Path = ROOT / "doc"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    ingestion_batch_size: int = Field(default=100, ge=1, le=500)
    ingestion_workers: int = Field(default=2, ge=1, le=8)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    connectors: ConnectorConfig = Field(default_factory=ConnectorConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    grok: GrokConfig = Field(default_factory=GrokConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @field_validator("fixflow_api_token", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("fixflow_api_token")
    @classmethod
    def validate_api_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and (
            len(value.get_secret_value()) < 32
            or not all("!" <= character <= "~" for character in value.get_secret_value())
        ):
            raise ValueError("FIXFLOW_API_TOKEN must contain at least 32 printable ASCII characters without spaces")
        return value

    @model_validator(mode="after")
    def validate_pool(self) -> "Settings":
        if self.db_pool_size + self.db_max_overflow <= self.ingestion_workers:
            raise ValueError("The DB pool must have spare capacity for ingestion status updates")
        return self

    @property
    def data_dir(self) -> Path:
        value = self.fixflow_data_dir.expanduser()
        return value if value.is_absolute() else (ROOT / "backend" / value).resolve()

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    def sqlalchemy_url(self) -> URL:
        if self.database_url:
            url = make_url(self.database_url.get_secret_value())
            if url.get_backend_name() != "postgresql":
                raise ValueError("DATABASE_URL must use PostgreSQL")
            return url.set(drivername="postgresql+asyncpg")
        if not self.postgres_password:
            raise ValueError("Set DATABASE_URL or POSTGRES_PASSWORD in backend/.env")
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
