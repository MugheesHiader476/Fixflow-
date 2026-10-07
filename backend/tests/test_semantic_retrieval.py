"""Actual pgvector security/configuration gates and query/fallback regressions."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, update

from backend.config import get_settings
from backend.db.models import ConnectorAccount, DocumentChunk, KnowledgeSource
from backend.db.session import get_session_factory
from backend.repositories.prepared import prepared_source
from backend.repositories.retrieval import search_chunks
from backend.repositories.vectors import VectorRepository
from backend.services.embeddings import EmbeddingError, embed_source, queued_embedding_sources
from backend.services.ingestion import ingest_source
from backend.services.ollama import OllamaEmbeddingProvider
from backend.services.retrieval import dense_retrieve, query_embedding, retrieve_sources
from backend.services.retrieval_config import retrieval_pin


class QueryProvider:
    def __init__(self, dimension: int = 768, *, unavailable: bool = False) -> None:
        self.dimension = dimension
        self.unavailable = unavailable
        self.inputs: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.inputs.extend(texts)
        if self.unavailable:
            raise EmbeddingError("Local embedding service unavailable")
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings, pin = get_settings(), retrieval_pin()
    monkeypatch.setattr(settings, "ollama_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "embedding_model", pin.model)
    monkeypatch.setattr(settings, "embedding_model_digest", pin.model_digest)
    monkeypatch.setattr(settings, "embedding_dim", pin.dimension)
    monkeypatch.setattr(settings, "embedding_profile", pin.formatting_version)


async def source_fixture(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> UUID:
    response = await client.post(
        "/api/documents",
        data={"content": "# Retrieval proof\n\nUniquevectorproof authorized evidence café 日本語."},
    )
    assert response.status_code == 202
    source_id = UUID(response.json()["id"])
    await ingest_source(source_id)
    configure(monkeypatch)
    assert await embed_source(source_id, QueryProvider(), "user_test")
    return source_id


@pytest.mark.anyio
async def test_query_format_dimension_and_unavailable(database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    provider = QueryProvider()
    vector = await query_embedding(" connection trouble ", provider)
    assert len(vector) == 768 and vector[0] == 1
    assert provider.inputs == ["task: search result | query:  connection trouble "]
    with pytest.raises(ValueError, match="match EMBEDDING_DIM"):
        await query_embedding("query", QueryProvider(3))
    with pytest.raises(EmbeddingError):
        await query_embedding("query", QueryProvider(unavailable=True))
    for text in (" ", "🚀" * 9000):
        with pytest.raises(ValueError):
            await query_embedding(text, provider)
    monkeypatch.setattr(get_settings(), "embedding_model_digest", "a" * 64)
    with pytest.raises(ValueError, match="pinned"):
        await query_embedding("query", provider)


@pytest.mark.anyio
async def test_dense_authorized_metadata_and_deterministic_ranking(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = await source_fixture(client, monkeypatch)
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        prepared = await prepared_source(db, source_id, "user_test")
        assert prepared
        result = await dense_retrieve(db, "proof", provider=QueryProvider())
        assert {m.chunk_id for m in result.matches} == {c.chunk_id for c in prepared.chunks}
        for match in result.matches:
            assert match.source_id == source_id and match.distance == 0
            assert match.metadata["source_context"] == prepared.canonical.source.context
            assert match.metadata["source_hash"] == prepared.canonical.source.sha256
            assert match.metadata["provenance"]
            assert match.metadata["embedding_identity"]
        again = await dense_retrieve(db, "proof", provider=QueryProvider())
        assert [m.chunk_id for m in result.matches] == [m.chunk_id for m in again.matches]
        db.info["owner_id"] = "other_account"
        assert not (await dense_retrieve(db, "proof", provider=QueryProvider())).matches


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["inactive", "expired", "failed", "processing", "revoked", "hash", "v1"])
async def test_dense_source_exclusion(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    source_id = await source_fixture(client, monkeypatch)
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, source_id)
        assert source
        if change == "inactive":
            source.is_active = False
        elif change == "expired":
            source.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change in {"failed", "processing"}:
            source.status = change
        elif change == "hash":
            source.file_hash = "a" * 64
        elif change == "v1":
            for chunk in await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id)):
                chunk.meta = {**chunk.meta, "contract_version": 1}
        else:
            account = ConnectorAccount(
                owner_id="user_test", provider="slack", external_account_id="test-workspace",
                display_name="Test", status="disconnected",
                authentication_status="revoked",
            )
            db.add(account)
            await db.flush()
            source.connector_account_id = account.id
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        assert not (await dense_retrieve(db, "proof", provider=QueryProvider())).matches


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["model_digest", "formatting_version", "config_hash", "input_hash", "truncate"])
async def test_wrong_embedding_identity_excluded(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, key: str,
) -> None:
    source_id = await source_fixture(client, monkeypatch)
    async with get_session_factory().begin() as db:
        for chunk in await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id)):
            stored = chunk.meta["embedding_identity"]
            assert isinstance(stored, dict)
            identity = dict(stored)
            identity[key] = True if key == "truncate" else "wrong"
            chunk.meta = {**chunk.meta, "embedding_identity": identity}
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        assert not (await dense_retrieve(db, "proof", provider=QueryProvider())).matches


@pytest.mark.anyio
async def test_no_vectors_and_keyword_fallback(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    source_id = await source_fixture(client, monkeypatch)
    monkeypatch.setattr(get_settings(), "retrieval_mode", "dense")

    async def fail(_: list[str]) -> list[list[float]]:
        raise EmbeddingError("unavailable")

    async def unavailable(self: OllamaEmbeddingProvider, texts: list[str]) -> list[list[float]]:
        return await fail(texts)

    monkeypatch.setattr(OllamaEmbeddingProvider, "embed", unavailable)
    async with get_session_factory().begin() as db:
        db.info["owner_id"] = "user_test"
        expected = await search_chunks(db, "Uniquevectorproof")
        assert expected and await retrieve_sources(db, "Uniquevectorproof") == expected
        await VectorRepository(db).delete_source_vectors(source_id)
        assert not await VectorRepository(db).search_pinned([1.0] + [0.0] * 767, retrieval_pin())
        assert await retrieve_sources(db, "Uniquevectorproof") == expected


@pytest.mark.anyio
async def test_expiry_during_query_embedding_excludes_source(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = await source_fixture(client, monkeypatch)

    class ExpiringProvider(QueryProvider):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            async with get_session_factory().begin() as db:
                await db.execute(update(KnowledgeSource).where(KnowledgeSource.id == source_id).values(
                    access_expires_at=datetime.now(UTC) + timedelta(milliseconds=20),
                ))
            await asyncio.sleep(0.04)
            return await super().embed(texts)

    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        await db.execute(select(KnowledgeSource.id))  # Transaction starts before expiry.
        assert not (await dense_retrieve(db, "proof", provider=ExpiringProvider())).matches


@pytest.mark.anyio
async def test_embedding_restart_queue_is_controlled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = await source_fixture(client, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_auto_process", False)
    assert await queued_embedding_sources() == []
    monkeypatch.setattr(settings, "embedding_auto_process", True)
    for state in ("not_configured", "complete", "failed", "pending", "processing"):
        async with get_session_factory().begin() as db:
            await db.execute(update(KnowledgeSource).where(KnowledgeSource.id == source_id).values(
                status="ready_for_embedding", embedding_status=state,
            ))
        assert await queued_embedding_sources() == ([source_id] if state in {"pending", "processing"} else [])


@pytest.mark.anyio
async def test_actual_dense_benchmark_and_hybrid_paths(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services.retrieval import hybrid_retrieve  # noqa: PLC0415
    from scripts import benchmark_retrieval  # noqa: PLC0415
    from scripts.benchmark_embeddings import Query  # noqa: PLC0415

    source_id = await source_fixture(client, monkeypatch)
    monkeypatch.setattr(benchmark_retrieval, "OWNER", "user_test")

    async def embed(self: OllamaEmbeddingProvider, texts: list[str]) -> list[list[float]]:
        return await QueryProvider().embed(texts)

    monkeypatch.setattr(OllamaEmbeddingProvider, "embed", embed)
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        prepared = await prepared_source(db, source_id, "user_test")
        assert prepared
        result = await hybrid_retrieve(db, "Uniquevectorproof")
        assert result.sources and result.metadata[result.sources[0].id]["source_id"] == str(source_id)
        assert result.metadata[result.sources[0].id]["provenance"]
        query: Query = {
            "id": "test", "type": "code", "text": "Uniquevectorproof", "gold_document": "test",
            "gold_chunk_ids": [c.chunk_id for c in prepared.chunks],
        }
    for method in ("keyword", "dense", "hybrid"):
        report = await benchmark_retrieval.run(method, [query], 3)
        assert report["recall_at_10"] == report["ndcg_at_10"] == 1
        assert report["errors"] == []
