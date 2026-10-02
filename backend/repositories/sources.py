from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.schemas.models import EmbeddingStatus, IngestionFormat, KnowledgeKind, SourceStatus
from backend.schemas.models import KnowledgeSource as SourceResponse
from backend.services.access import owner_id


def source_response(source: KnowledgeSource, document_count: int = 0, chunk_count: int = 0) -> SourceResponse:
    return SourceResponse(
        id=str(source.id),
        source_id=str(source.id),
        name=source.name,
        kind=cast(KnowledgeKind, source.source_type),
        source_type=cast(KnowledgeKind, source.source_type),
        status=cast(SourceStatus, source.status),
        ingestion_format=cast(IngestionFormat, source.ingestion_format),
        embedding_status=cast(EmbeddingStatus, source.embedding_status),
        embedding_error=source.embedding_error,
        documents=document_count,
        document_count=document_count,
        chunks=chunk_count,
        chunk_count=chunk_count,
        updated=source.updated_at,
        created_at=source.created_at,
        technology=source.technology,
        version=source.version,
        url=source.url,
        error_message=source.error_message,
        detail=source.error_message or f"{document_count} documents · {chunk_count} chunks",
    )


async def list_sources(session: AsyncSession, source_id: UUID | None = None) -> list[SourceResponse]:
    docs_query = select(Document.source_id, func.count().label("count")).group_by(Document.source_id)
    chunks_query = select(DocumentChunk.source_id, func.count().label("count")).group_by(DocumentChunk.source_id)
    if source_id is not None:
        docs_query = docs_query.where(Document.source_id == source_id)
        chunks_query = chunks_query.where(DocumentChunk.source_id == source_id)
    docs, chunks = docs_query.subquery(), chunks_query.subquery()
    statement = (
        select(KnowledgeSource, func.coalesce(docs.c.count, 0), func.coalesce(chunks.c.count, 0))
        .outerjoin(docs, KnowledgeSource.id == docs.c.source_id)
        .outerjoin(chunks, KnowledgeSource.id == chunks.c.source_id)
        .where(KnowledgeSource.owner_id == owner_id(session))
        .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id)
    )
    if source_id is not None:
        statement = statement.where(KnowledgeSource.id == source_id)
    rows = await session.execute(statement)
    return [source_response(source, documents, chunks) for source, documents, chunks in rows]


async def delete_source(session: AsyncSession, source_id: UUID) -> bool:
    result = await session.execute(
        delete(KnowledgeSource)
        .where(KnowledgeSource.id == source_id, KnowledgeSource.owner_id == owner_id(session))
        .returning(KnowledgeSource.id)
    )
    return result.scalar_one_or_none() is not None
