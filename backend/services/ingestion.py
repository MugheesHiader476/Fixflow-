import asyncio
import logging
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from uuid import UUID

from asyncpg import PostgresError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.loaders import loader_for
from backend.processing.okf import concept_metadata, maybe_parse_concept
from backend.repositories.corpus import chunk_documents, document_values, insert_chunks, insert_documents, json_metadata

logger = logging.getLogger(__name__)
PENDING_STATUSES = ("uploaded", "processing", "chunked")


class IngestionError(RuntimeError):
    """A failed job has rolled back and its safe error has been persisted."""


def load_documents(path: Path, source_id: UUID, digest: str) -> Iterator[dict[str, object]]:
    loader = loader_for(path)
    if loader is None:
        raise ValueError("Unsupported document type")
    for index, document in enumerate(loader.lazy_load()):
        content = document.page_content.strip()
        if not content:
            continue
        metadata = json_metadata(document.metadata)
        if path.suffix.lower() == ".md":
            concept = maybe_parse_concept(content, path.name)
            if concept is not None:
                content = concept.body.strip()
                metadata.update(concept_metadata(concept))
                if not content:
                    continue
        metadata.update(
            {
                "file_hash": digest,
                "filename": path.name,
                "file_extension": path.suffix.lower(),
                "document_index": index,
                "source": str(path),
            }
        )
        yield document_values(source_id, content, metadata, index)


def next_batch(iterator: Iterator[dict[str, object]], size: int) -> list[dict[str, object]]:
    return list(islice(iterator, size))


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
            if source is None or source.status not in PENDING_STATUSES:
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
            iterator = load_documents(path, source_id, source.file_hash)
            while rows := await asyncio.to_thread(next_batch, iterator, settings.ingestion_batch_size):
                await insert_documents(session, rows)
                chunks = iter(chunk_documents(source_id, rows, settings.chunk_size, settings.chunk_overlap))
                while batch := await asyncio.to_thread(next_batch, chunks, settings.ingestion_batch_size):
                    await insert_chunks(session, batch)
            count = await session.scalar(
                select(func.count()).select_from(Document).where(Document.source_id == source_id)
            )
            if not count:
                raise ValueError("No extractable text found. Scanned PDFs require OCR before upload.")
            chunks_count = await session.scalar(
                select(func.count()).select_from(DocumentChunk).where(DocumentChunk.source_id == source_id)
            )
            if not chunks_count:
                raise ValueError("No extractable text chunks found. Upload a document with more text.")
            source.status = "chunked"
            await session.flush()
            source.status = "ready_for_embedding"
            source.error_message = None
    except Exception as error:
        # Loader/driver failures must roll back every document and chunk in this job.
        logger.error("Ingestion failed for %s (%s)", source_id, type(error).__name__)
        message = "Document processing failed. Check the file format and installed ingestion dependencies."
        if isinstance(error, ValueError) and str(error).startswith(("Remote URL", "The uploaded", "No extractable")):
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
                        .where(KnowledgeSource.status.in_(PENDING_STATUSES))
                        .order_by(KnowledgeSource.created_at)
                        .limit(get_settings().ingestion_workers)
                    )
                )
            if ids:
                await asyncio.gather(*(ingest_source(source_id) for source_id in ids), return_exceptions=True)
        except (SQLAlchemyError, PostgresError, OSError, ValueError, TimeoutError) as error:
            logger.error("Ingestion queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(2)
