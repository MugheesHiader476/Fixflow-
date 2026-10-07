"""Native transport and prepared-source persistence/security regressions."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, update

from backend.config import Settings, get_settings
from backend.db.models import DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.repositories.prepared import prepared_source
from backend.repositories.vectors import EmbeddingInput, VectorRepository
from backend.services.embedding_profiles import PROFILES
from backend.services.embeddings import EmbeddingError, embed_source
from backend.services.ingestion import ingest_source
from backend.services.ollama import OllamaEmbeddingProvider

DIGEST = "a" * 64


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://127.0.0.1.evil",
        "http://localhost/x",
        "http://user:secret@localhost",
        "http://localhost?x=1",
        "https://localhost",
    ],
)
def test_local_transport_cannot_downgrade_remote_https(url: str) -> None:
    with pytest.raises(ValueError):
        Settings.validate_ollama_url(url)
    with pytest.raises(ValueError):
        Settings.validate_embedding_url("http://example.com/embed")


def test_profiles_do_not_change_canonical_text() -> None:
    text = "Unicode café 日本語 🚀 code(value)"
    for profile in PROFILES.values():
        assert text in profile.document(text, "Example")
        assert text in profile.query(text)
    assert PROFILES["embeddinggemma:300m"].document(text, "heading" * 10000) == "title: none | text: " + text
    assert PROFILES["qwen3-embedding:0.6b"].document(text) == text
    assert PROFILES["nomic-embed-text:v1.5"].document(text).startswith("search_document: ")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "malformed",
        "count",
        "dimension",
        "nan",
        "inf",
        "zero",
        "timeout",
        "unavailable",
        "oversized",
        "digest",
        "model",
        "boolean",
    ],
)
async def test_native_ollama_validation(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    real_client = httpx.AsyncClient
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "test-model", "digest": DIGEST if case != "digest" else "b" * 64}]}
            )
        payload = json.loads(request.content)
        assert payload["truncate"] is False and payload["input"] == ["Text"]
        if case == "timeout":
            raise httpx.ReadTimeout("secret", request=request)
        if case == "unavailable":
            raise httpx.ConnectError("secret", request=request)
        if case == "oversized":
            return httpx.Response(400, json={"error": "input exceeds context secret"})
        if case == "malformed":
            return httpx.Response(200, text="not json secret")
        vectors = {
            "count": [],
            "dimension": [[1.0]],
            "nan": [[float("nan"), 1.0, 0.0]],
            "inf": [[float("inf"), 1.0, 0.0]],
            "zero": [[0.0, 0.0, 0.0]],
            "boolean": [[True, 0.0, 1.0]],
        }.get(case, [[1.0, 0.0, 0.0]])
        # json.dumps deliberately exercises hostile NaN/Inf serialization.
        return httpx.Response(
            200, text=json.dumps({"model": "wrong" if case == "model" else "test-model", "embeddings": vectors})
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    provider = OllamaEmbeddingProvider("http://127.0.0.1:11434", "test-model", 3, DIGEST)
    if case == "valid":
        assert await provider.embed(["Text"]) == [[1.0, 0.0, 0.0]]
        assert len(calls) == 3
    else:
        with pytest.raises(EmbeddingError) as error:
            await provider.embed(["Text"])
        assert "secret" not in str(error.value)


class RecordingProvider:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[1.0, 2.0, 3.0] for _ in texts]


async def source_fixture(client: httpx.AsyncClient) -> UUID:
    uploaded = await client.post(
        "/api/documents",
        data={
            "content": "# Embedding contract\n\nEmbedbeginmarker evidence.\n\n"
            + "Middle evidence stays authorized. " * 80
            + "\n\nEmbedendmarker."
        },
    )
    source_id = UUID(uploaded.json()["id"])
    await ingest_source(source_id)
    return source_id


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_model", "test-only-model")
    monkeypatch.setattr(settings, "embedding_dim", 3)
    monkeypatch.setattr(settings, "embedding_model_digest", DIGEST)
    monkeypatch.setattr(settings, "embedding_batch_size", 2)


@pytest.mark.anyio
async def test_validated_input_persistence_identity_reuse_and_keyword(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = await source_fixture(client)
    configure(monkeypatch)
    async with get_session_factory()() as db:
        prepared = await prepared_source(db, source_id, "user_test")
        assert prepared
        expected = [c.retrieval_content for c in prepared.chunks]
        before = [c.model_dump() for c in prepared.chunks]
    provider = RecordingProvider()
    assert not await embed_source(source_id, provider, "user_other")
    assert provider.texts == []
    assert await embed_source(source_id, provider, "user_test")
    assert provider.texts == expected
    async with get_session_factory()() as db:
        current = await prepared_source(db, source_id, "user_test")
        assert current and [c.model_dump() for c in current.chunks] == before
        records = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id)))
        assert len(records) == len(expected)
        assert all(c.embedding is not None and len(c.embedding) == 3 for c in records)
        assert all(
            isinstance(c.meta["embedding_identity"], dict)
            and c.meta["embedding_identity"].get("model_digest") == DIGEST
            for c in records
        )
    provider.texts.clear()
    assert await embed_source(source_id, provider, "user_test")
    assert provider.texts == []
    monkeypatch.setattr(get_settings(), "embedding_model_digest", "b" * 64)
    assert await embed_source(source_id, provider, "user_test")
    assert provider.texts == expected
    assert (await client.post("/api/debug", json={"error": "Embedendmarker"})).json()["sources"]


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["hash", "version", "inactive", "owner", "projection", "legacy"])
async def test_source_changes_cannot_commit_stale_vectors(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    source_id = await source_fixture(client)
    configure(monkeypatch)

    class ChangingProvider(RecordingProvider):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            if not self.texts:
                async with get_session_factory().begin() as db:
                    if change in {"hash", "inactive", "owner"}:
                        changes: dict[str, dict[str, object]] = {
                            "hash": {"file_hash": "b" * 64},
                            "inactive": {"is_active": False},
                            "owner": {"owner_id": "user_other"},
                        }
                        values = changes[change]
                        await db.execute(
                            update(KnowledgeSource).where(KnowledgeSource.id == source_id).values(**values)
                        )
                    elif change == "projection":
                        await db.execute(
                            update(DocumentChunk).where(DocumentChunk.source_id == source_id).values(content="damaged")
                        )
                    else:
                        artifact = await db.get(IngestionArtifact, source_id)
                        assert artifact
                        payload = copy.deepcopy(artifact.result)
                        assert isinstance(payload["canonical"], dict)
                        assert isinstance(payload["canonical"]["source"], dict)
                        if change == "legacy":
                            payload["contract_version"] = 1
                        else:
                            payload["canonical"]["source"]["version"] = 999
                        artifact.result = payload
            return await super().embed(texts)

    assert not await embed_source(source_id, ChangingProvider(), "user_test")
    async with get_session_factory()() as db:
        assert (
            await db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.embedding.is_not(None)))
            == 0
        )
        source = await db.get(KnowledgeSource, source_id)
        assert source and source.status != "indexed"
        if change == "owner":
            assert source.owner_id == "user_other"
        if change == "inactive":
            assert not source.is_active


@pytest.mark.anyio
async def test_failed_persistence_rolls_back_all_vectors(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = await source_fixture(client)
    configure(monkeypatch)
    original = VectorRepository.insert_embeddings

    async def fail_after_write(
        self: VectorRepository, embeddings: list[EmbeddingInput], *, mark_indexed: bool = True
    ) -> int:
        # Force a late failure after actual SQL writes in the source transaction.
        await original(self, embeddings, mark_indexed=False)
        raise ValueError("test-only failure after SQL")

    monkeypatch.setattr(VectorRepository, "insert_embeddings", fail_after_write)
    assert not await embed_source(source_id, RecordingProvider(), "user_test")
    async with get_session_factory()() as db:
        assert (
            await db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.embedding.is_not(None)))
            == 0
        )
        assert await prepared_source(db, source_id, "user_test")


@pytest.mark.anyio
async def test_lease_expiry_uses_wall_clock_not_transaction_start(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = await source_fixture(client)
    configure(monkeypatch)

    class ExpiringProvider(RecordingProvider):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            if not self.texts:
                async with get_session_factory().begin() as db:
                    await db.execute(
                        update(KnowledgeSource)
                        .where(KnowledgeSource.id == source_id)
                        .values(
                            access_expires_at=datetime.now(UTC) + timedelta(milliseconds=30),
                        )
                    )
                await asyncio.sleep(0.06)
            return await super().embed(texts)

    assert not await embed_source(source_id, ExpiringProvider(), "user_test")
    async with get_session_factory()() as db:
        assert await prepared_source(db, source_id, "user_test") is None
        assert (
            await db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.embedding.is_not(None)))
            == 0
        )


def test_ollama_configuration_requires_pinned_profile_dimension_and_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    valid = {
        "ollama_url": "http://127.0.0.1:11434",
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_dim": 1024,
        "embedding_profile": "qwen-v1",
        "embedding_model_digest": DIGEST,
    }
    assert Settings.model_validate(valid).embedding_enabled
    for change in (
        {"embedding_dim": 768},
        {"embedding_profile": "plain-v1"},
        {"embedding_model_digest": None},
        {"embedding_api_url": "https://remote.example/embed"},
    ):
        with pytest.raises(ValueError):
            Settings.model_validate({**valid, **change})
