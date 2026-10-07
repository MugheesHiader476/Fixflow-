"""Authorized, verified source/chunk handoff for subsequent offline embedding experiments.

No provider calls, vectors or embedding generation occur here. Old JSONL/v1 rows require
re-ingestion from their original source before they qualify for this contract.
"""

from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document, DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.processing.pipeline.concepts import render_block
from backend.processing.pipeline.validation import validate_result
from backend.repositories.source_access import accessible_source
from backend.schemas.pipeline import Chunk, PipelineResult, digest


async def prepared_source(
    session: AsyncSession,
    source_id: UUID,
    owner_id: str,
    *,
    fresh_access: bool = False,
) -> PipelineResult | None:
    source = await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == source_id,
            KnowledgeSource.owner_id == owner_id,
            KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
            accessible_source(current_time=fresh_access),
        )
    )
    if source is None:
        return None
    artifact = await session.get(IngestionArtifact, source_id)
    if artifact is None:
        raise ValueError("Source requires reprocessing for the validated chunk contract")
    result = PipelineResult.model_validate(artifact.result)
    validate_result(result)
    context = result.canonical.source.context
    permissions = context.get("permissions")
    if (
        result.canonical.source.source_id != str(source_id)
        or result.canonical.source.sha256 != source.file_hash
        or not isinstance(permissions, dict)
        or permissions.get("application_owner") != owner_id
    ):
        raise ValueError("Persisted source registration or authorization mismatch")
    records = list(
        await session.scalars(
            select(DocumentChunk).where(DocumentChunk.source_id == source_id).order_by(DocumentChunk.chunk_index)
        )
    )
    documents = list(await session.scalars(select(Document).where(Document.source_id == source_id)))
    expected_documents = {uuid5(source_id, c.concept_id): c for c in result.concepts}
    if {d.id for d in documents} != set(expected_documents) or len(records) != len(result.chunks):
        raise ValueError("Incomplete persisted pipeline projection")
    by_block = {b.block_id: b for b in result.canonical.blocks}
    for document in documents:
        concept = expected_documents[document.id]
        if (
            document.content_hash != concept.content_hash
            or document.page_content != "\n\n".join(render_block(by_block[i]) for i in concept.source_block_ids)
            or document.title != concept.title
            or document.section != " / ".join(concept.section_path)
            or document.meta.get("canonical_hash") != result.canonical.content_hash
            or document.meta.get("pipeline_concept_id") != concept.concept_id
            or document.meta.get("source_context") != context
        ):
            raise ValueError("Persisted concept projection mismatch")
    for record, chunk in zip(records, result.chunks, strict=True):
        projected = Chunk.model_validate({key: record.meta[key] for key in Chunk.model_fields})
        if (
            projected != chunk
            or record.id != uuid5(source_id, chunk.chunk_id)
            or record.document_id != uuid5(source_id, chunk.concept_id)
            or record.chunk_id != chunk.chunk_id
            or record.chunk_index != chunk.order_index
            or record.content != chunk.raw_content
            or record.content_hash != chunk.content_hash
            or record.meta.get("document_id") != str(record.document_id)
            or record.meta.get("source_hash") != source.file_hash
            or record.meta.get("source_version") != result.canonical.source.version
            or record.meta.get("text_hash") != digest(chunk.retrieval_content)
            or record.meta.get("contract_version") != result.contract_version
            or record.meta.get("engine_version") != result.engine_version
            or record.meta.get("config_hash") != result.config_hash
        ):
            raise ValueError("Persisted chunk projection mismatch")
    return result
