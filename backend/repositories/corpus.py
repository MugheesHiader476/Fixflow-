"""Batch persistence shared by uploads and the optional JSONL importer."""

import hashlib
import json
from collections.abc import Iterable
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document, DocumentChunk
from backend.processing.chunking import records_for_source


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return dict(json.loads(json.dumps(value, default=str)))


def integer(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def document_values(source_id: UUID, content: str, metadata: dict[str, object], index: int) -> dict[str, object]:
    digest = content_hash(content)
    return {
        "id": uuid5(source_id, digest),
        "source_id": source_id,
        "document_index": index,
        "page_content": content,
        "content_hash": digest,
        "metadata": metadata,
        "page_number": integer(metadata["page"]) if "page" in metadata else None,
        "title": str(metadata["title"]) if metadata.get("title") else None,
        "section": str(metadata["section"]) if metadata.get("section") else None,
    }


def chunk_values(
    source_id: UUID, document_id: UUID, content: str, metadata: dict[str, object], index: int
) -> dict[str, object]:
    digest = content_hash(content)
    identifier = str(metadata.get("chunk_id") or hashlib.sha256(f"{source_id}:{digest}".encode()).hexdigest())
    return {
        "id": uuid5(source_id, f"chunk:{digest}"),
        "source_id": source_id,
        "document_id": document_id,
        "chunk_id": identifier if len(identifier) <= 128 else content_hash(identifier),
        "chunk_index": index,
        "content": content,
        "content_hash": digest,
        "metadata": metadata,
        "embedding": None,
        "embedding_model": None,
        "embedding_dimension": None,
    }


async def insert_documents(session: AsyncSession, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    result = await session.execute(
        insert(cast(Table, Document.__table__)).values(rows).on_conflict_do_nothing().returning(Document.id)
    )
    return len(result.all())


async def insert_chunks(session: AsyncSession, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    result = await session.execute(
        insert(cast(Table, DocumentChunk.__table__)).values(rows).on_conflict_do_nothing().returning(DocumentChunk.id)
    )
    return len(result.all())


def chunk_documents(
    source_id: UUID, rows: Iterable[dict[str, object]], chunk_size: int, overlap: int
) -> Iterable[dict[str, object]]:
    for row in rows:
        item = {"page_content": row["page_content"], "metadata": row["metadata"]}
        for record in records_for_source(item, integer(row["document_index"]), chunk_size, overlap, set()):
            metadata = json_metadata(record["metadata"])
            # Live document projections must be scoped to a source, even for identical uploads.
            metadata["chunk_id"] = content_hash(f"{source_id}:{metadata['chunk_id']}")
            yield chunk_values(
                source_id,
                UUID(str(row["id"])),
                str(record["page_content"]),
                metadata,
                integer(metadata.get("chunk_index")),
            )
