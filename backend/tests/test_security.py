import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from backend.config import Settings
from backend.main import configured_origins
from backend.services.uploads import safe_filename, validated_remote_url


@pytest.mark.parametrize("origin", ["*", "file:///tmp", "https://user:secret@example.com", "https://example.com/path"])
def test_configured_origins_rejects_unsafe_values(origin: str) -> None:
    with pytest.raises(ValueError):
        configured_origins(origin)


def test_configured_origins_normalizes_and_deduplicates() -> None:
    assert configured_origins("https://example.com/, https://example.com") == ["https://example.com"]


def test_windows_traversal_and_invalid_extension() -> None:
    assert safe_filename("..\\..\\private.md") == "private.md"
    with pytest.raises(HTTPException):
        safe_filename(".exe")


def test_remote_url_credentials_rejected() -> None:
    with pytest.raises(HTTPException):
        validated_remote_url("https://user:password@example.com")


def test_configuration_requires_postgresql_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        Settings().sqlalchemy_url()
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(database_url=SecretStr("sqlite:///db")).sqlalchemy_url()
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        Settings(chunk_size=10, chunk_overlap=10)


def test_embedding_configuration_rejects_unsafe_endpoint_and_exhausted_pool() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(embedding_api_url="http://provider.example/embeddings")
    with pytest.raises(ValueError, match="requires EMBEDDING_MODEL"):
        Settings(embedding_api_url="https://provider.example/embeddings", embedding_model=None, embedding_dim=None)
    with pytest.raises(ValueError, match="both ingestion and embedding"):
        Settings(
            embedding_api_url="https://provider.example/embeddings",
            embedding_model="test-model",
            embedding_dim=3,
            db_pool_size=4,
            db_max_overflow=0,
            ingestion_workers=2,
        )


@pytest.mark.parametrize("token", ["a" * 31, "a" * 32 + "\n", "a" * 32 + " "])
def test_gateway_secret_requires_header_safe_characters(token: str) -> None:
    with pytest.raises(ValueError, match="printable ASCII"):
        Settings(fixflow_api_token=SecretStr(token))
