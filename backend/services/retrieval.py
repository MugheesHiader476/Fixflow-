"""Query embedding and strict dense retrieval; lexical retrieval remains the fallback."""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import DocumentChunk, KnowledgeSource
from backend.repositories.prepared import prepared_source
from backend.repositories.retrieval import search_chunks
from backend.repositories.source_access import accessible_source
from backend.repositories.vectors import DenseMatch, VectorRepository
from backend.schemas.models import SourceDoc
from backend.schemas.pipeline import PipelineResult
from backend.services.access import owner_id
from backend.services.embeddings import EmbeddingError, EmbeddingProvider
from backend.services.ollama import OllamaEmbeddingProvider
from backend.services.retrieval_config import retrieval_pin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DenseResult:
    matches: list[DenseMatch]
    embedding_seconds: float
    search_seconds: float
    validation_seconds: float


async def query_embedding(text: str, provider: EmbeddingProvider | None = None) -> list[float]:
    pin = retrieval_pin()
    pin.check_runtime()
    if not text.strip() or len(text.encode("utf-8")) > 32_000:
        raise ValueError("Query must be nonblank and at most 32000 UTF-8 bytes")
    if provider is None:
        settings = get_settings()
        provider = OllamaEmbeddingProvider(
            settings.ollama_url or "", pin.model, pin.dimension, pin.model_digest,
            timeout=settings.retrieval_timeout_seconds,
        )
    vectors = await provider.embed([pin.profile.query(text)])
    if len(vectors) != 1:
        raise ValueError("Query embedding count mismatch")
    vector = vectors[0]
    VectorRepository.validate_vector(vector, pin.dimension)
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid query embedding norm")
    return [value / norm for value in vector]


async def dense_retrieve(
    db: AsyncSession, text: str, limit: int = 5, *, provider: EmbeddingProvider | None = None,
) -> DenseResult:
    if not 1 <= limit <= 100:
        raise ValueError("Search limit must be between 1 and 100")
    start = time.perf_counter()
    # Bound inference only: cancellation cannot leave a DB transaction aborted.
    async with asyncio.timeout(get_settings().retrieval_timeout_seconds):
        vector = await query_embedding(text, provider)
    embedding_end = time.perf_counter()
    matches = await VectorRepository(db).search_pinned(vector, retrieval_pin(), limit)
    search_end = time.perf_counter()
    # Validate projections and exact embedding inputs once per source, per request.
    # No shared cache can outlive an authorization lease or a source update.
    valid: list[DenseMatch] = []
    sources: dict[UUID, PipelineResult | None] = {}
    pin = retrieval_pin()
    for match in matches:
        if match.source_id not in sources:
            try:
                sources[match.source_id] = await prepared_source(
                    db, match.source_id, owner_id(db), fresh_access=True,
                )
            except (ValueError, KeyError):
                logger.warning("Dense candidate rejected: invalid prepared projection")
                sources[match.source_id] = None
        prepared = sources[match.source_id]
        if prepared is None:
            continue
        chunk = next((chunk for chunk in prepared.chunks if chunk.chunk_id == match.chunk_id), None)
        if chunk is None:
            continue
        label = next(concept.title for concept in prepared.concepts if concept.concept_id == chunk.concept_id)
        expected = pin.identity(pin.profile.document(chunk.retrieval_content, label))
        if match.metadata.get("embedding_identity") == expected:
            valid.append(match)
    if valid:
        # Recheck access/status and projection freshness after validation/inference.
        current_rows = await db.execute(
            select(DocumentChunk.chunk_id, DocumentChunk.meta)
            .join(KnowledgeSource, KnowledgeSource.id == DocumentChunk.source_id)
            .where(
                DocumentChunk.chunk_id.in_([match.chunk_id for match in valid]),
                KnowledgeSource.owner_id == owner_id(db), accessible_source(current_time=True),
                KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
                DocumentChunk.meta["source_hash"].astext == KnowledgeSource.file_hash,
                DocumentChunk.embedding.is_not(None),
            )
        )
        current = {row.chunk_id: row.meta for row in current_rows}
        valid = [match for match in valid if current.get(match.chunk_id) == match.metadata]
    return DenseResult(valid, embedding_end - start, search_end - embedding_end, time.perf_counter() - search_end)


def dense_sources(matches: list[DenseMatch]) -> list[SourceDoc]:
    return [
        SourceDoc(
            id=match.chunk_id, type="github" if match.source_type == "github" else "docs",
            title=match.title, publisher="Knowledge base", url=match.url or "", relevance=0,
            excerpt=match.content[:6000], used=True,
        )
        for match in matches
    ]


async def retrieve_sources(db: AsyncSession, text: str, limit: int = 5) -> list[SourceDoc]:
    """Safe lexical fallback for absent/incompatible vectors or local inference failure."""
    if get_settings().retrieval_mode == "keyword":
        return await search_chunks(db, text, limit)
    try:
        result = await dense_retrieve(db, text, limit)
        if result.matches:
            return dense_sources(result.matches)
    except (EmbeddingError, ValueError, TimeoutError):
        logger.warning("Dense retrieval unavailable; using keyword retrieval")
    return await search_chunks(db, text, limit)


def reciprocal_rank_fusion(rankings: list[list[str]], limit: int, constant: int = 60) -> list[str]:
    """Each distinct chunk votes once per input ranking; scores never use distance scales."""
    if not 1 <= limit <= 100 or constant < 1:
        raise ValueError("Invalid fusion bounds")
    scores: dict[str, float] = {}
    for ranking in rankings:
        unique = list(dict.fromkeys(ranking))
        for rank, chunk_id in enumerate(unique, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (constant + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]


@dataclass(frozen=True)
class HybridResult:
    sources: list[SourceDoc]
    metadata: dict[str, dict[str, object]]
    dense: DenseResult
    keyword_seconds: float
    fusion_seconds: float


async def hybrid_retrieve(db: AsyncSession, text: str, limit: int = 5) -> HybridResult:
    """Untuned experimental RRF: 30 candidates per method, constant 60, no reranker."""
    if not 1 <= limit <= 30:
        raise ValueError("Hybrid limit must be between 1 and 30")
    dense = await dense_retrieve(db, text, 30)
    start = time.perf_counter()
    lexical = await search_chunks(db, text, 30)
    keyword_end = time.perf_counter()
    ordered = reciprocal_rank_fusion(
        [[source.id for source in lexical], [match.chunk_id for match in dense.matches]], limit,
    )
    # SQL authorization at the final handoff, including lease expiry during inference.
    rows = await db.execute(
        select(DocumentChunk, KnowledgeSource)
        .join(KnowledgeSource, KnowledgeSource.id == DocumentChunk.source_id)
        .where(
            DocumentChunk.chunk_id.in_(ordered), KnowledgeSource.owner_id == owner_id(db),
            accessible_source(current_time=True),
            KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
        )
        .execution_options(populate_existing=True)
    )
    available = {chunk.chunk_id: (chunk, source) for chunk, source in rows}
    sources, metadata = [], {}
    for chunk_id in ordered:
        if chunk_id not in available:
            continue
        chunk, source = available[chunk_id]
        sources.append(SourceDoc(
            id=chunk_id, type="github" if source.source_type == "github" else "docs", title=source.name,
            publisher="Knowledge base", url=source.url or "", relevance=0, excerpt=chunk.content[:6000], used=True,
        ))
        metadata[chunk_id] = {**chunk.meta, "source_id": str(chunk.source_id), "document_id": str(chunk.document_id)}
    return HybridResult(sources, metadata, dense, keyword_end - start, time.perf_counter() - keyword_end)
