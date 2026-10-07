"""Optional real embedding endpoint; keyword retrieval remains unchanged."""

import asyncio
import json
import logging
import math
from typing import Protocol
from uuid import UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.db.models import ConnectorAccount, DocumentChunk, KnowledgeSource
from backend.db.session import get_session_factory
from backend.repositories.prepared import prepared_source
from backend.repositories.source_access import accessible_source
from backend.repositories.vectors import EmbeddingInput, VectorRepository
from backend.schemas.pipeline import digest
from backend.services.access import LEGACY_OWNER
from backend.services.embedding_profiles import ModelProfile, embedding_identity, profile_for

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
    settings = get_settings()
    if settings.ollama_url:
        from backend.services.ollama import OllamaEmbeddingProvider  # noqa: PLC0415

        return OllamaEmbeddingProvider(
            settings.ollama_url,
            settings.embedding_model or "",
            settings.embedding_dim or 0,
            settings.embedding_model_digest or "",
            settings.embedding_timeout_seconds,
        )
    return HttpEmbeddingProvider() if settings.embedding_api_url else None


async def embed_source(source_id: UUID, provider: EmbeddingProvider, trusted_owner_id: str | None = None) -> bool:
    settings = get_settings()
    lock_key = int.from_bytes(source_id.bytes[:8], "big", signed=True)
    source_hash: str | None = None
    owner: str | None = None
    try:
        async with get_session_factory().begin() as db:
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock_key))):
                return False
            source = await db.scalar(
                select(KnowledgeSource).where(
                    KnowledgeSource.id == source_id,
                    accessible_source(),
                    KnowledgeSource.owner_id != LEGACY_OWNER,
                    KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
                    KnowledgeSource.embedding_status != "failed",
                )
            )
            if source is None or (trusted_owner_id is not None and source.owner_id != trusted_owner_id):
                return False
            owner, source_hash = source.owner_id, source.file_hash
            connector_id = source.connector_account_id
            prepared = await prepared_source(db, source_id, owner, fresh_access=True)
            if prepared is None or not prepared.chunks:
                raise EmbeddingError("No authorized validated chunks available")
            snapshot = digest(prepared.model_dump_json())
            model, dimension = VectorRepository.configuration()
            profile = (
                profile_for(model)
                if settings.ollama_url
                else ModelProfile(model, dimension, settings.embedding_profile)
            )
            records = {
                c.id: c for c in await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id))
            }
            pending: list[tuple[UUID, str, dict[str, object]]] = []
            for chunk in prepared.chunks:
                record_id = uuid5(source_id, chunk.chunk_id)
                text = profile.document(
                    chunk.retrieval_content,
                    next(c.title for c in prepared.concepts if c.concept_id == chunk.concept_id),
                )
                identity = embedding_identity(model, settings.embedding_model_digest, dimension, profile.version, text)
                record = records[record_id]
                if not (
                    settings.embedding_model_digest
                    and record.embedding is not None
                    and record.embedding_model == model
                    and record.embedding_dimension == dimension
                    and record.meta.get("embedding_identity") == identity
                ):
                    pending.append((record_id, text, identity))
            # Visible progress is conditional: it cannot resurrect a concurrently removed/updated source.
            async with get_session_factory().begin() as status_db:
                changed = await status_db.scalar(
                    update(KnowledgeSource)
                    .where(
                        KnowledgeSource.id == source_id,
                        KnowledgeSource.owner_id == owner,
                        KnowledgeSource.file_hash == source_hash,
                        accessible_source(),
                        KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
                    )
                    .values(status="embedding", embedding_status="processing", embedding_error=None)
                    .returning(KnowledgeSource.id)
                )
                if changed is None:
                    raise EmbeddingError("Source changed before embedding")
            writes: list[EmbeddingInput] = []
            for start in range(0, len(pending), settings.embedding_batch_size):
                batch = pending[start : start + settings.embedding_batch_size]
                vectors = await provider.embed([item[1] for item in batch])
                if len(vectors) != len(batch):
                    raise EmbeddingError("Embedding service returned an incomplete batch")
                for item, vector in zip(batch, vectors, strict=True):
                    VectorRepository.validate_vector(vector, dimension)
                    norm = math.hypot(*vector)
                    writes.append(EmbeddingInput(item[0], [value / norm for value in vector], item[2]))
            # Provider calls hold no row locks. Lock and refresh authoritative state only at commit.
            db.expire_all()
            # Connector lifecycle writers lock accounts before sources; use the same order.
            if connector_id:
                await db.scalar(select(ConnectorAccount).where(ConnectorAccount.id == connector_id).with_for_update())
            source = await db.scalar(
                select(KnowledgeSource)
                .where(
                    KnowledgeSource.id == source_id,
                    KnowledgeSource.owner_id == owner,
                )
                .with_for_update()
            )
            if source is None or source.connector_account_id != connector_id:
                raise EmbeddingError("Source removed during embedding")
            current = await prepared_source(db, source_id, owner, fresh_access=True)
            if current is None or source.file_hash != source_hash or digest(current.model_dump_json()) != snapshot:
                raise EmbeddingError("Source version or authorization changed during embedding")
            await VectorRepository(db).insert_embeddings(writes, mark_indexed=False)
            source.status = "indexed"
            source.embedding_status = "complete"
            source.embedding_error = None
        return True
    except (EmbeddingError, ValueError, SQLAlchemyError) as error:
        async with get_session_factory().begin() as db:
            await db.execute(
                update(KnowledgeSource)
                .where(
                    KnowledgeSource.id == source_id,
                    KnowledgeSource.owner_id == owner,
                    KnowledgeSource.file_hash == source_hash,
                    accessible_source(),
                    KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
                )
                .values(
                    status="ready_for_embedding",
                    embedding_status="failed",
                    embedding_error="Embedding failed. Check provider configuration and retry.",
                )
            )
        logger.warning("Embedding failed for %s (%s)", source_id, type(error).__name__)
        return False


async def embedding_worker() -> None:
    while True:
        try:
            provider = get_embedding_provider()
            if provider is not None:
                async with get_session_factory()() as db:
                    ids = list(
                        await db.scalars(
                            select(KnowledgeSource.id)
                            .where(
                                KnowledgeSource.status.in_(("ready_for_embedding", "embedding")),
                                KnowledgeSource.embedding_status != "failed",
                                KnowledgeSource.owner_id != LEGACY_OWNER,
                                accessible_source(),
                            )
                            .order_by(KnowledgeSource.created_at)
                            .limit(get_settings().embedding_workers)
                        )
                    )
                await asyncio.gather(*(embed_source(source_id, provider) for source_id in ids))
        except Exception as error:  # noqa: BLE001 - deliberate background-service boundary.
            logger.error("Embedding queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(2)
