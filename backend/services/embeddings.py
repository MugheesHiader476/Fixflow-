"""Optional real embedding endpoint; keyword retrieval remains unchanged."""

import asyncio
import json
import logging
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update

from backend.config import get_settings
from backend.db.models import DocumentChunk, KnowledgeSource
from backend.db.session import get_session_factory
from backend.repositories.vectors import EmbeddingInput, VectorRepository
from backend.services.access import LEGACY_OWNER

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Safe provider failure; raw network/provider details must not reach clients."""


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingRow(BaseModel):
    model_config = ConfigDict(strict=True)
    index: int = Field(ge=0)
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    data: list[EmbeddingRow]


class HttpEmbeddingProvider:
    """JSON contract: POST {model,input}; response {data:[{index,embedding}]}."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()
        if not settings.embedding_api_url or not settings.embedding_model or not settings.embedding_dim:
            raise EmbeddingError("Embedding pipeline is not configured")
        headers = {}
        if settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {settings.embedding_api_key.get_secret_value()}"
        try:
            async with (
                httpx.AsyncClient(timeout=settings.embedding_timeout_seconds, follow_redirects=False) as client,
                client.stream(
                    "POST",
                    settings.embedding_api_url,
                    headers=headers,
                    json={"model": settings.embedding_model, "input": texts},
                ) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 16 * 1024 * 1024:
                        raise ValueError("Embedding response exceeds processing limit")
                payload = EmbeddingResponse.model_validate(json.loads(body))
            ordered = sorted(payload.data, key=lambda row: row.index)
            if [row.index for row in ordered] != list(range(len(texts))):
                raise ValueError("Embedding response indexes do not match the batch")
            for row in ordered:
                VectorRepository.validate_vector(row.embedding, settings.embedding_dim)
            return [row.embedding for row in ordered]
        except (httpx.HTTPError, ValidationError, ValueError, TypeError, RecursionError) as error:
            raise EmbeddingError("Embedding service failed or returned invalid vectors") from error


def get_embedding_provider() -> EmbeddingProvider | None:
    return HttpEmbeddingProvider() if get_settings().embedding_api_url else None


async def embed_source(source_id: UUID, provider: EmbeddingProvider) -> None:
    settings = get_settings()
    lock_key = int.from_bytes(source_id.bytes[:8], "big", signed=True)
    try:
        async with get_session_factory().begin() as db:
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock_key))):
                return
            source = await db.get(KnowledgeSource, source_id)
            if (
                source is None
                or not source.is_active
                or source.status not in {"ready_for_embedding", "embedding"}
                or source.embedding_status == "failed"
            ):
                return
            total = await db.scalar(
                select(func.count()).select_from(DocumentChunk).where(DocumentChunk.source_id == source_id)
            )
            if not total:
                raise EmbeddingError("No chunks available for embedding")
            # Commit a visible processing state separately; vector writes stay atomic.
            async with get_session_factory().begin() as status_db:
                await status_db.execute(
                    update(KnowledgeSource)
                    .where(KnowledgeSource.id == source_id)
                    .values(status="embedding", embedding_status="processing", embedding_error=None)
                )
            source.status = "embedding"
            source.embedding_status = "processing"
            model, dimension = VectorRepository.configuration()
            statement = (
                select(DocumentChunk)
                .where(
                    DocumentChunk.source_id == source_id,
                    (DocumentChunk.embedding.is_(None))
                    | (DocumentChunk.embedding_model != model)
                    | (DocumentChunk.embedding_dimension != dimension),
                )
                .order_by(DocumentChunk.id)
            )
            stream = await db.stream_scalars(statement)
            async for chunks in stream.partitions(settings.embedding_batch_size):
                vectors = await provider.embed([chunk.content for chunk in chunks])
                if len(vectors) != len(chunks):
                    raise EmbeddingError("Embedding service returned an incomplete batch")
                await VectorRepository(db).insert_embeddings(
                    [EmbeddingInput(chunk.id, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
                )
            source.status = "indexed"
            source.embedding_status = "complete"
            source.embedding_error = None
    except (EmbeddingError, ValueError) as error:
        async with get_session_factory().begin() as db:
            await db.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.id == source_id)
                .values(
                    status="ready_for_embedding",
                    embedding_status="failed",
                    embedding_error="Embedding failed. Check provider configuration and retry.",
                )
            )
        logger.warning("Embedding failed for %s (%s)", source_id, type(error).__name__)


async def embedding_worker() -> None:
    while True:
        provider = get_embedding_provider()
        if provider is not None:
            try:
                async with get_session_factory()() as db:
                    ids = list(
                        await db.scalars(
                            select(KnowledgeSource.id)
                            .where(
                                KnowledgeSource.status.in_(("ready_for_embedding", "embedding")),
                                KnowledgeSource.embedding_status != "failed",
                                KnowledgeSource.owner_id != LEGACY_OWNER,
                                KnowledgeSource.is_active.is_(True),
                            )
                            .order_by(KnowledgeSource.created_at)
                            .limit(get_settings().ingestion_workers)
                        )
                    )
                await asyncio.gather(*(embed_source(source_id, provider) for source_id in ids))
            except Exception as error:  # noqa: BLE001 - deliberate background-service boundary.
                logger.error("Embedding queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(2)
