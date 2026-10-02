"""Transactional projections retain compatible documents/chunks and preserve unchanged vectors."""

from uuid import UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document, DocumentChunk, IngestionArtifact
from backend.processing.okf import concept_metadata, parse_concept
from backend.processing.pipeline.concepts import render_block
from backend.schemas.pipeline import PipelineResult


async def previous_result(session: AsyncSession, source_id: UUID) -> PipelineResult | None:
    artifact = await session.get(IngestionArtifact, source_id)
    return PipelineResult.model_validate(artifact.result) if artifact else None


async def persist_result(session: AsyncSession, source_id: UUID, result: PipelineResult) -> None:
    documents = {d.id: d for d in await session.scalars(select(Document).where(Document.source_id == source_id))}
    chunks = {
        c.chunk_id: c for c in await session.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id))
    }
    by_block = {b.block_id: b for b in result.canonical.blocks}
    document_ids = {c.concept_id: uuid5(source_id, c.concept_id) for c in result.concepts}
    retained_documents = set(document_ids.values())
    retained_chunks = {c.chunk_id for c in result.chunks}
    # Remove only obsolete artifacts from this source. Other sources and session history are untouched.
    await session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.source_id == source_id, DocumentChunk.chunk_id.not_in(retained_chunks)
        )
    )
    await session.execute(
        delete(Document).where(Document.source_id == source_id, Document.id.not_in(retained_documents))
    )
    for index, concept in enumerate(result.concepts):
        document_id = document_ids[concept.concept_id]
        parsed = parse_concept(concept.markdown, concept.concept_id + ".md")
        metadata = concept_metadata(parsed)
        uploaded = result.canonical.metadata.get("uploaded_okf")
        if isinstance(uploaded, dict):
            # Existing API consumers retain uploaded OKF metadata as before; generated artifact stays distinct.
            metadata["okf"] = {"concept_id": result.canonical.source.filename[:-3], "frontmatter": uploaded}
        metadata.update(
            {
                "pipeline_concept_id": concept.concept_id,
                "source_block_ids": concept.source_block_ids,
                "canonical_hash": result.canonical.content_hash,
                "filename": result.canonical.source.filename,
            }
        )
        values = {
            "document_index": index,
            "page_content": "\n\n".join(render_block(by_block[i]) for i in concept.source_block_ids),
            "title": concept.title,
            "section": " / ".join(concept.section_path),
            "meta": metadata,
            "content_hash": concept.content_hash,
            "page_number": next(
                (by_block[i].provenance.page for i in concept.source_block_ids if by_block[i].provenance.page), None
            ),
        }
        document = documents.get(document_id)
        if document is None:
            session.add(Document(id=document_id, source_id=source_id, **values))
        else:
            for key, value in values.items():
                setattr(document, key, value)
    await session.flush()
    for index, chunk in enumerate(result.chunks):
        metadata = chunk.model_dump(mode="json")
        metadata["tokenizer"] = result.canonical.metadata["tokenizer"]
        concept_document = document_ids[chunk.concept_id]
        concept = next(c for c in result.concepts if c.concept_id == chunk.concept_id)
        parsed_metadata = concept_metadata(parse_concept(concept.markdown, concept.concept_id + ".md"))
        uploaded = result.canonical.metadata.get("uploaded_okf")
        metadata["okf"] = (
            {"concept_id": result.canonical.source.filename[:-3], "frontmatter": uploaded}
            if isinstance(uploaded, dict)
            else parsed_metadata["okf"]
        )
        values = {
            "document_id": concept_document,
            "chunk_index": index,
            "content": chunk.raw_content,
            "content_hash": chunk.content_hash,
            "meta": metadata,
        }
        record = chunks.get(chunk.chunk_id)
        if record is None:
            session.add(
                DocumentChunk(
                    id=uuid5(source_id, chunk.chunk_id), source_id=source_id, chunk_id=chunk.chunk_id, **values
                )
            )
        else:
            if record.content_hash != chunk.content_hash:
                record.embedding = record.embedding_model = record.embedding_dimension = None
            for key, value in values.items():
                setattr(record, key, value)
    artifact = await session.get(IngestionArtifact, source_id)
    payload = result.model_dump(mode="json")
    if artifact is None:
        session.add(IngestionArtifact(source_id=source_id, result=payload))
    else:
        artifact.result = payload
