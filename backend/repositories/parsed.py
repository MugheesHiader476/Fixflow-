"""Read validated canonical output only after SQL authorizes the current source."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import IngestionArtifact, KnowledgeSource
from backend.processing.pipeline.validation import validate_result
from backend.repositories.source_access import accessible_source
from backend.schemas.pipeline import PipelineResult


async def parsed_identity(session: AsyncSession, source_id: UUID, trusted_owner_id: str) -> tuple[str, str] | None:
    """Current (source sha256, canonical hash) after the same SQL authorization, without decoding the artifact.

    Workflow revalidation only needs to prove that previously captured evidence still refers to the
    same authorized, unchanged source. Content reads continue to use `parsed_source`, which validates
    the whole canonical document before any block is returned.
    """
    canonical = IngestionArtifact.result["canonical"]
    row = (
        await session.execute(
            select(KnowledgeSource.file_hash, canonical["content_hash"].astext)
            .join(IngestionArtifact, IngestionArtifact.source_id == KnowledgeSource.id)
            .where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.owner_id == trusted_owner_id,
                KnowledgeSource.status == "parsed",
                accessible_source(current_time=True),
                canonical["source"]["source_id"].astext == str(source_id),
                canonical["source"]["sha256"].astext == KnowledgeSource.file_hash,
                canonical["source"]["context"]["permissions"]["application_owner"].astext == trusted_owner_id,
            )
        )
    ).one_or_none()
    return (str(row[0]), str(row[1])) if row and row[1] else None


async def parsed_source(session: AsyncSession, source_id: UUID, trusted_owner_id: str) -> PipelineResult | None:
    source = await session.scalar(
        select(KnowledgeSource)
        .where(
            KnowledgeSource.id == source_id,
            KnowledgeSource.owner_id == trusted_owner_id,
            KnowledgeSource.status == "parsed",
            accessible_source(current_time=True),
        )
        .execution_options(populate_existing=True)
    )
    if source is None:
        return None
    artifact = await session.get(IngestionArtifact, source_id, populate_existing=True)
    if artifact is None:
        raise ValueError("Missing parsing artifact")
    result = PipelineResult.model_validate(artifact.result)
    validate_result(result)
    permissions = result.canonical.source.context.get("permissions")
    if (
        result.canonical.source.source_id != str(source_id)
        or result.canonical.source.sha256 != source.file_hash
        or not isinstance(permissions, dict)
        or permissions.get("application_owner") != trusted_owner_id
    ):
        raise ValueError("Parsed artifact registration or ownership mismatch")
    return result
