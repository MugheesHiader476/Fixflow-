from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

from backend.connectors.config import ConnectorConfig
from backend.processing.pipeline.config import PipelineConfig

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ROOT / "backend/.env"),
        extra="ignore",
        case_sensitive=False,
        env_nested_delimiter="__",
    )

    fixflow_api_token: SecretStr | None = None
    embedding_api_url: str | None = None
    embedding_api_key: SecretStr | None = None
    ollama_url: str | None = None
    embedding_model_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    embedding_profile: Literal["plain-v1", "qwen-v1", "gemma-v1", "nomic-v1"] = "plain-v1"
    embedding_auto_process: bool = False
    retrieval_mode: Literal["keyword", "dense"] = "keyword"
    retrieval_timeout_seconds: float = Field(default=10, gt=0, le=120)
    generation_ollama_url: str | None = None
    generation_model: str | None = Field(default=None, max_length=200)
    generation_model_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    generation_timeout_seconds: float = Field(default=90, gt=0, le=90)
    generation_context_tokens: int = Field(default=8192, ge=4096, le=32768)
    generation_max_tokens: int = Field(default=1024, ge=256, le=2048)
    generation_concurrency: int = Field(default=1, ge=1, le=4)
    embedding_workers: int = Field(default=1, ge=1, le=4)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=120)
    embedding_batch_size: int = Field(default=4, ge=1, le=100)
    max_extracted_chars: int = Field(default=5_000_000, ge=1)
    max_document_chunks: int = Field(default=10_000, ge=1)
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
    chunk_size: int = Field(default=3000, gt=0)
    chunk_overlap: int = Field(default=400, ge=0)
    embedding_dim: int | None = Field(default=None, ge=1, le=16000)
    embedding_model: str | None = Field(default=None, max_length=200)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    connectors: ConnectorConfig = Field(default_factory=ConnectorConfig)

    @field_validator(
        "fixflow_api_token",
        "embedding_api_key",
        "embedding_api_url",
        "embedding_model",
        "ollama_url",
        "embedding_model_digest",
        "generation_ollama_url",
        "generation_model",
        "generation_model_digest",
        mode="before",
    )
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

    @field_validator("embedding_api_url")
    @classmethod
    def validate_embedding_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        from urllib.parse import urlsplit  # noqa: PLC0415

        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("EMBEDDING_API_URL must be an HTTPS URL without credentials or fragment")
        return value

    @field_validator("embedding_dim", mode="before")
    @classmethod
    def optional_dimension(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("ollama_url", "generation_ollama_url")
    @classmethod
    def validate_ollama_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        from urllib.parse import urlsplit  # noqa: PLC0415

        parsed = urlsplit(value)
        # Local HTTP is an explicit transport, never an exception for remote providers.
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("OLLAMA_URL must be an HTTP loopback origin without credentials or path")
        return value.rstrip("/")

    @property
    def embedding_enabled(self) -> bool:
        return bool(self.embedding_api_url or self.ollama_url)

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        generation_fields = (self.generation_ollama_url, self.generation_model, self.generation_model_digest)
        if any(generation_fields) and not all(generation_fields):
            raise ValueError("Local answers require GENERATION_OLLAMA_URL, GENERATION_MODEL and a pinned digest")
        if self.generation_model and not self.generation_model.strip():
            raise ValueError("GENERATION_MODEL cannot be blank")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.db_pool_size + self.db_max_overflow <= self.ingestion_workers:
            raise ValueError("The DB pool must have spare capacity for ingestion status updates")
        if self.embedding_enabled and self.db_pool_size + self.db_max_overflow <= (
            self.ingestion_workers + 2 * self.embedding_workers
        ):
            raise ValueError("The DB pool must have spare capacity for both ingestion and embedding workers")
        if self.embedding_enabled and not (self.embedding_model and self.embedding_dim):
            raise ValueError("An embedding endpoint requires EMBEDDING_MODEL and EMBEDDING_DIM")
        if self.ollama_url and self.embedding_api_url:
            raise ValueError("Configure exactly one embedding transport")
        if self.ollama_url:
            from backend.services.embedding_profiles import profile_for  # noqa: PLC0415

            profile = profile_for(self.embedding_model or "")
            if not self.embedding_model_digest or self.embedding_profile != profile.version:
                raise ValueError("Ollama requires a pinned model digest and matching formatting profile")
            if self.embedding_dim != profile.dimension:
                raise ValueError("Ollama dimension must match the pinned model profile")
        return self

    @property
    def generation_enabled(self) -> bool:
        return bool(self.generation_ollama_url and self.generation_model and self.generation_model_digest)

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
