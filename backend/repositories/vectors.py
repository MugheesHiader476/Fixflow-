"""Vector storage/search interface for a future, externally supplied embedding pipeline."""

import math
from dataclasses import dataclass
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import column, exists, func, select, update, values
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import DocumentChunk, KnowledgeSource
from backend.services.access import owner_id


class EmbeddingPipelineNotConfigured(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Embedding pipeline not configured")


@dataclass(frozen=True)
class EmbeddingInput:
    chunk_id: UUID
    vector: list[float]


@dataclass(frozen=True)
class VectorMatch:
    chunk_id: str
    source_id: UUID
    content: str
    metadata: dict[str, object]
    distance: float


class VectorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def configuration() -> tuple[str, int]:
        settings = get_settings()
        if not settings.embedding_model or not settings.embedding_dim:
            raise EmbeddingPipelineNotConfigured()
        return settings.embedding_model, settings.embedding_dim

    @staticmethod
    def validate_vector(vector: list[float], dimension: int) -> None:
        if len(vector) != dimension or not all(math.isfinite(value) for value in vector) or not any(vector):
            raise ValueError("Embedding must be finite, nonzero, and match EMBEDDING_DIM")

    async def insert_embeddings(self, embeddings: list[EmbeddingInput]) -> int:
        model, dimension = self.configuration()
        if len({item.chunk_id for item in embeddings}) != len(embeddings):
            raise ValueError("Duplicate chunk IDs in embedding batch")
        for item in embeddings:
            self.validate_vector(item.vector, dimension)
        source_ids: set[UUID] = set()
        size = get_settings().ingestion_batch_size
        # A savepoint prevents partially applied batches even if a caller catches validation errors.
        async with self.session.begin_nested():
            for start in range(0, len(embeddings), size):
                batch = embeddings[start : start + size]
                data = values(column("chunk_uuid", PGUUID()), column("vector", Vector(dimension)), name="vectors").data(
                    [(item.chunk_id, item.vector) for item in batch]
                )
                result = await self.session.scalars(
                    update(DocumentChunk)
                    .where(DocumentChunk.id == data.c.chunk_uuid)
                    .values(
                        embedding=data.c.vector.cast(Vector(dimension)),
                        embedding_model=model,
                        embedding_dimension=dimension,
                    )
                    .returning(DocumentChunk.source_id)
                )
                changed = list(result)
                if len(changed) != len(batch):
                    raise ValueError("An embedding references an unknown chunk")
                source_ids.update(changed)
            missing = exists(
                select(DocumentChunk.id).where(
                    DocumentChunk.source_id == KnowledgeSource.id,
                    (DocumentChunk.embedding.is_(None))
                    | (DocumentChunk.embedding_model != model)
                    | (DocumentChunk.embedding_dimension != dimension),
                )
            )
            if source_ids:
                await self.session.execute(
                    update(KnowledgeSource)
                    .where(
                        KnowledgeSource.id.in_(source_ids),
                        ~missing,
                        KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
                    )
                    .values(status="indexed")
                )
        return len(embeddings)

    async def similarity_search(self, vector: list[float], limit: int = 5) -> list[VectorMatch]:
        model, dimension = self.configuration()
        self.validate_vector(vector, dimension)
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        if not await self.session.scalar(
            select(
                exists().where(
                    DocumentChunk.source_id == KnowledgeSource.id,
                    KnowledgeSource.owner_id == owner_id(self.session),
                    DocumentChunk.embedding.is_not(None),
                    DocumentChunk.embedding_model == model,
                    DocumentChunk.embedding_dimension == dimension,
                )
            )
        ):
            raise EmbeddingPipelineNotConfigured()
        matching = (
            select(DocumentChunk)
            .join(KnowledgeSource, DocumentChunk.source_id == KnowledgeSource.id)
            .where(
                KnowledgeSource.owner_id == owner_id(self.session),
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_model == model,
                DocumentChunk.embedding_dimension == dimension,
            )
            .cte("matching_vectors")
            .prefix_with("MATERIALIZED")
        )
        distance = matching.c.embedding.cosine_distance(vector)
        rows = await self.session.execute(
            select(
                matching.c.chunk_id,
                matching.c.source_id,
                matching.c.content,
                matching.c.metadata,
                distance.label("distance"),
            )
            .order_by(distance, matching.c.chunk_id)
            .limit(limit)
        )
        return [VectorMatch(*row) for row in rows]

    async def delete_source_vectors(self, source_id: UUID) -> None:
        await self.session.execute(
            update(DocumentChunk)
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.source_id.in_(
                    select(KnowledgeSource.id).where(KnowledgeSource.owner_id == owner_id(self.session))
                ),
            )
            .values(
                embedding=None,
                embedding_model=None,
                embedding_dimension=None,
            )
        )
        await self.session.execute(
            update(KnowledgeSource)
            .where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.owner_id == owner_id(self.session),
                KnowledgeSource.status == "indexed",
            )
            .values(status="ready_for_embedding", embedding_status="not_configured", embedding_error=None)
        )

    async def count_embedded_chunks(self, source_id: UUID | None = None) -> int:
        query = (
            select(func.count())
            .select_from(DocumentChunk)
            .join(KnowledgeSource, DocumentChunk.source_id == KnowledgeSource.id)
            .where(DocumentChunk.embedding.is_not(None), KnowledgeSource.owner_id == owner_id(self.session))
        )
        if source_id is not None:
            query = query.where(DocumentChunk.source_id == source_id)
        return int(await self.session.scalar(query) or 0)
