"""Persist canonical parsing output without producing post-parsing artifacts."""

from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document, IngestionArtifact, KnowledgeSource
from backend.processing.pipeline.validation import validate_result
from backend.repositories.evidence import refresh_index
from backend.schemas.pipeline import CanonicalDocument, PipelineResult


async def previous_result(session: AsyncSession, source_id: UUID) -> PipelineResult | None:
    artifact = await session.get(IngestionArtifact, source_id)
    if artifact is None:
        return None
    if artifact.result.get("contract_version") == 3:
        return PipelineResult.model_validate(artifact.result)
    # Older artifacts supply version history only. Their old config hash cannot hit the new cache.
    canonical = CanonicalDocument.model_validate(artifact.result["canonical"])
    return PipelineResult(
        canonical=canonical,
        engine_version=canonical.version,
        config_hash="legacy-requires-reparse",
        stage_hashes={"source": canonical.source.sha256, "canonical": canonical.content_hash},
    )


async def persist_result(session: AsyncSession, source_id: UUID, result: PipelineResult) -> None:
    validate_result(result)
    source = await session.get(KnowledgeSource, source_id)
    canonical = result.canonical
    permissions = canonical.source.context.get("permissions")
    if (
        source is None
        or canonical.source.source_id != str(source_id)
        or source.file_hash != canonical.source.sha256
        or not isinstance(permissions, dict)
        or permissions.get("application_owner") != source.owner_id
    ):
        raise ValueError("Pipeline registration or ownership mismatch")
    # One whole parsed-document projection; original ordered blocks/structure remain in the artifact.
    identifier = uuid5(source_id, "parsed-document")
    document = await session.get(Document, identifier)
    values = {
        "document_index": 0,
        "page_content": "\n\n".join(block.content for block in canonical.blocks),
        "title": canonical.source.filename,
        "content_hash": canonical.content_hash,
        "meta": {
            "contract_version": result.contract_version,
            "canonical_document_id": canonical.document_id,
            "source_context": canonical.source.context,
            "source_version": canonical.source.version,
        },
    }
    if document is None:
        session.add(Document(id=identifier, source_id=source_id, **values))
    else:
        for key, value in values.items():
            setattr(document, key, value)
    # Legacy projections remain stored for data preservation; no runtime reads them for search.
    artifact = await session.get(IngestionArtifact, source_id)
    payload = result.model_dump(mode="json")
    if artifact is None:
        session.add(IngestionArtifact(source_id=source_id, result=payload))
    else:
        artifact.result = payload
    # Only sources explicitly opted into evidence search receive a derived index.
    await refresh_index(session, source, result)
