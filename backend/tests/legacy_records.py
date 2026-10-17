"""Legacy row fixtures for verifying preserved database constraints; no content splitting."""

import hashlib
import json
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document, DocumentChunk


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
