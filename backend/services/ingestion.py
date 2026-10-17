import asyncio
import logging
from pathlib import Path
from uuid import UUID

from asyncpg import PostgresError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.db.models import KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.execution import execute_pipeline as run_pipeline
from backend.processing.pipeline.runner import PipelineError
from backend.repositories.pipeline import persist_result, previous_result

logger = logging.getLogger(__name__)
PENDING_STATUSES = ("uploaded", "processing", "chunked")


class IngestionError(RuntimeError):
    """A failed job has rolled back and its safe error has been persisted."""


async def set_status(source_id: UUID, status: str, error: str | None = None) -> None:
    async with get_session_factory().begin() as session:
        await session.execute(
            update(KnowledgeSource).where(KnowledgeSource.id == source_id).values(status=status, error_message=error)
        )


async def ingest_source(source_id: UUID) -> None:
    settings = get_settings()
    # Transaction-scoped advisory locks make retries and multiple API workers idempotent.
    lock_key = int.from_bytes(source_id.bytes[:8], "big", signed=True)
    try:
        async with get_session_factory().begin() as session:
            locked = await session.scalar(select(func.pg_try_advisory_xact_lock(lock_key)))
            if not locked:
                return
            source = await session.get(KnowledgeSource, source_id)
            if source is None or source.status not in PENDING_STATUSES or not source.is_active:
                return
            await set_status(source_id, "processing")
            if source.path is None:
                raise ValueError("Remote URL ingestion is not configured; upload a file or paste documentation.")
            path = Path(source.path)
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(settings.upload_dir.resolve())
            ):
                raise ValueError("The uploaded document is unavailable.")
            config = PipelineConfig.model_validate(
                {
                    **settings.pipeline.model_dump(),
                    "max_characters": settings.max_extracted_chars,
                }
            )
            previous = await previous_result(session, source_id)
            stored_context = source.ingestion_metadata.get("source_envelope")
            context: dict[str, object] = (
                dict(stored_context)
                if isinstance(stored_context, dict)
                else {
                    "source_id": str(source_id),
                    "source_type": source.source_type,
                    "filename": source.name,
                    "content_hash": source.file_hash,
                    "provenance": {"original_uri": f"source:{source_id}"},
                }
            )
            permissions = context.get("permissions")
            if isinstance(permissions, dict) and permissions.get("application_owner") != source.owner_id:
                raise ValueError("Source envelope ownership mismatch")
            context["permissions"] = {
                **(permissions if isinstance(permissions, dict) else {}),
                "visibility": "private",
                "application_owner": source.owner_id,
                "provider_resource": str(context.get("external_id", source_id)),
            }
            result = await asyncio.to_thread(
                run_pipeline,
                path,
                str(source_id),
                config,
                previous=previous,
                force=bool(source.ingestion_metadata.get("force")),
                strict_okf=source.ingestion_format == "okf",
                source_context=context,
            )
            if result.canonical.source.sha256 != source.file_hash:
                raise ValueError("The uploaded document changed after registration.")
            await persist_result(session, source_id, result)
            source.ingestion_metadata = {
                **source.ingestion_metadata,
                "force": False,
                "pipeline_contract_version": result.contract_version,
                "parsed_blocks": len(result.canonical.blocks),
                "version": result.canonical.source.version,
                "mime_type": result.canonical.source.mime_type,
                "registration": result.canonical.source.model_dump(mode="json"),
                "profile": result.canonical.profile.model_dump(mode="json"),
            }
            source.status = "parsed"
            source.error_message = None
            source.embedding_status = "not_configured"
            source.embedding_error = None
    except Exception as error:
        # Parser/driver failures must roll back every artifact and document in this job.
        logger.error("Ingestion failed for %s (%s)", source_id, type(error).__name__)
        message = "Document processing failed. Check the file format and installed ingestion dependencies."
        if isinstance(error, PipelineError):
            message = str(error)
        if isinstance(error, ValueError) and str(error).startswith(
            ("Remote URL", "The uploaded", "No extractable", "Invalid OKF")
        ):
            message = str(error)
        await set_status(source_id, "failed", message)
        raise IngestionError(message) from error


async def ingestion_worker() -> None:
    """Database-backed pending jobs survive API restarts; locks coordinate workers."""
    while True:
        try:
            async with get_session_factory()() as session:
                ids = list(
                    await session.scalars(
                        select(KnowledgeSource.id)
                        .where(KnowledgeSource.status.in_(PENDING_STATUSES), KnowledgeSource.is_active.is_(True))
                        .order_by(KnowledgeSource.created_at)
                        .limit(get_settings().ingestion_workers)
                    )
                )
            if ids:
                await asyncio.gather(*(ingest_source(source_id) for source_id in ids), return_exceptions=True)
        except (SQLAlchemyError, PostgresError, OSError, ValueError, TimeoutError) as error:
            logger.error("Ingestion queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(2)
