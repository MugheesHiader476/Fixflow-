"""Optional, resumable JSONL migration. Run: python -m scripts.import_jsonl_to_db --help."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections.abc import Generator, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import exists, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.db.session import close_database, get_session_factory
from backend.repositories.corpus import (
    chunk_documents,
    chunk_values,
    content_hash,
    document_values,
    insert_chunks,
    insert_documents,
    integer,
    json_metadata,
)
from backend.services.access import LEGACY_OWNER

logger = logging.getLogger(__name__)


@dataclass
class Progress:
    lines: int = 0
    skipped: int = 0
    documents: int = 0
    chunks: int = 0


@dataclass
class Record:
    content: str
    metadata: dict[str, object]
    digest: str
    name: str
    index: int


def records(path: Path, progress: Progress) -> Generator[Record]:
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            progress.lines += 1
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                progress.skipped += 1
                continue
            if not isinstance(value, dict) or not isinstance(value.get("page_content"), str):
                progress.skipped += 1
                continue
            content = value["page_content"]
            if not content.strip() or "\x00" in content:
                progress.skipped += 1
                continue
            metadata = json_metadata(value.get("metadata"))
            identity = str(
                metadata.get("source") or metadata.get("relative_path") or metadata.get("filename") or path.stem
            )
            digest = str(metadata.get("file_hash") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                digest = content_hash(identity)
            name = str(metadata.get("filename") or Path(identity).name)[:255]
            yield Record(content, metadata, digest.lower(), name, integer(metadata.get("document_index"), line_number))


async def source_ids(db: AsyncSession, batch: list[Record]) -> dict[str, UUID]:
    unique = {record.digest: record for record in batch}
    rows = [
        {
            "id": uuid4(),
            "owner_id": LEGACY_OWNER,
            "name": item.name or "Imported documentation",
            "source_type": "docs",
            "file_hash": digest,
            "status": "failed",
            "error_message": "Import incomplete: no chunks available yet.",
            "technology": str(item.metadata["technology"])[:200] if item.metadata.get("technology") else None,
            "version": str(item.metadata["version"])[:200] if item.metadata.get("version") else None,
        }
        for digest, item in unique.items()
    ]
    await db.execute(
        insert(KnowledgeSource)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["owner_id", "file_hash"], index_where=KnowledgeSource.external_identity.is_(None)
        )
    )
    result = await db.execute(
        select(KnowledgeSource.file_hash, KnowledgeSource.id).where(
            KnowledgeSource.file_hash.in_(unique),
            KnowledgeSource.owner_id == LEGACY_OWNER,
            KnowledgeSource.external_identity.is_(None),
        )
    )
    return dict(result.tuples().all())


async def import_chunk_batch(db: AsyncSession, batch: list[Record], ids: dict[str, UUID], progress: Progress) -> None:
    keys = {(ids[item.digest], item.index) for item in batch}
    found = await db.execute(
        select(Document.source_id, Document.document_index, Document.id)
        .where(
            tuple_(Document.source_id, Document.document_index).in_(keys),
        )
        .order_by(Document.id)
    )
    documents = {(source, index): identifier for source, index, identifier in found}
    missing = []
    chunks = []
    for item in batch:
        source_id = ids[item.digest]
        document_id = documents.get((source_id, item.index))
        if document_id is None:
            # Chunk-only exports cannot reconstruct original pages; retain each fragment explicitly.
            metadata = {**item.metadata, "imported_from_chunk": True}
            row = document_values(source_id, item.content, metadata, item.index)
            document_id = UUID(str(row["id"]))
            missing.append(row)
        chunks.append(
            chunk_values(
                source_id,
                document_id,
                item.content,
                item.metadata,
                integer(item.metadata.get("chunk_index")),
            )
        )
    progress.documents += await insert_documents(db, missing)
    progress.chunks += await insert_chunks(db, chunks)


async def mark_ready(db: AsyncSession, ids: set[UUID]) -> None:
    chunks = exists(select(DocumentChunk.id).where(DocumentChunk.source_id == KnowledgeSource.id))
    unembedded = exists(
        select(DocumentChunk.id).where(
            DocumentChunk.source_id == KnowledgeSource.id,
            DocumentChunk.embedding.is_(None),
        )
    )
    await db.execute(
        update(KnowledgeSource)
        .where(
            KnowledgeSource.id.in_(ids),
            chunks,
            unembedded,
        )
        .values(status="ready_for_embedding", error_message=None)
    )


async def import_file(path: Path, is_chunks: bool, generate_chunks: bool, progress: Progress, size: int) -> None:
    settings = get_settings()
    iterator = records(path, progress)
    try:
        while batch := list(islice(iterator, size)):
            async with get_session_factory().begin() as db:
                ids = await source_ids(db, batch)
                if is_chunks:
                    await import_chunk_batch(db, batch, ids, progress)
                else:
                    rows = [
                        document_values(ids[item.digest], item.content, item.metadata, item.index) for item in batch
                    ]
                    progress.documents += await insert_documents(db, rows)
                    if generate_chunks:
                        for source_id in set(ids.values()):
                            chunks = iter(
                                chunk_documents(
                                    source_id,
                                    (row for row in rows if row["source_id"] == source_id),
                                    settings.chunk_size,
                                    settings.chunk_overlap,
                                )
                            )
                            while chunk_batch := list(islice(chunks, size)):
                                progress.chunks += await insert_chunks(db, chunk_batch)
                await mark_ready(db, set(ids.values()))
            logger.info(
                "%s: %s lines, %s skipped, %s documents, %s chunks inserted",
                path.name,
                progress.lines,
                progress.skipped,
                progress.documents,
                progress.chunks,
            )
    finally:
        iterator.close()


async def import_jsonl(documents: Path | None, chunks: Path | None, batch_size: int = 100) -> Progress:
    if not 1 <= batch_size <= 500:
        raise ValueError("Batch size must be between 1 and 500")
    progress = Progress()
    if documents is not None:
        await import_file(documents, False, chunks is None, progress, batch_size)
    if chunks is not None:
        await import_file(chunks, True, False, progress, batch_size)
    return progress


async def run(documents: Path | None, chunks: Path | None, batch_size: int) -> None:
    try:
        progress = await import_jsonl(documents, chunks, batch_size)
        logger.info("Complete: %s", progress)
    finally:
        await close_database()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--chunks", type=Path)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    if not args.documents and not args.chunks:
        root = get_settings().data_dir / "processed"
        args.documents = root / "documents.jsonl" if (root / "documents.jsonl").is_file() else None
        args.chunks = root / "chunks.jsonl" if (root / "chunks.jsonl").is_file() else None
    if not args.documents and not args.chunks:
        parser.error("No JSONL found; supply --documents and/or --chunks")
    if not 1 <= args.batch_size <= 500:
        parser.error("Batch size must be between 1 and 500")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run(args.documents, args.chunks, args.batch_size))


if __name__ == "__main__":
    main()
