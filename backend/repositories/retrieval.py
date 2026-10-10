"""Shared lexical retrieval; no model calls or fabricated similarity scores."""

import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import DocumentChunk, KnowledgeSource
from backend.repositories.source_access import accessible_source
from backend.schemas.models import SourceDoc
from backend.services.access import owner_id

SEARCH_TERMS = re.compile(r"[^\W_]{3,}", re.UNICODE)
STOP_WORDS = {"the", "and", "for", "with", "this", "that", "how", "does", "what", "from", "can", "you"}


def source_excerpt(
    chunk_id: str, source_id: UUID, content: str, metadata: dict[str, object],
    title: str, source_type: str, url: str | None,
) -> SourceDoc:
    labels: list[str] = []
    section = metadata.get("section_path")
    if isinstance(section, list):
        labels.append(" / ".join(str(part) for part in section)[:500])
    version = metadata.get("source_version")
    if isinstance(version, int):
        labels.append(f"Version {version}")
    provenance = metadata.get("provenance")
    if isinstance(provenance, list):
        for item in provenance[:3]:
            if isinstance(item, dict):
                if item.get("page"):
                    labels.append(f"Page {item['page']}")
                if item.get("line_start"):
                    labels.append(f"Lines {item['line_start']}-{item.get('line_end') or item['line_start']}")
    selectors = metadata.get("unit_slices")
    if isinstance(selectors, list):
        fragments = 0
        for selector in selectors:
            continuation = selector.get("continuation") if isinstance(selector, dict) else None
            if isinstance(continuation, dict) and isinstance(continuation.get("index"), int):
                labels.append(f"Part {continuation['index'] + 1}/{continuation.get('count')} (fragment)")
                fragments += 1
                if fragments == 3:
                    break
    source_hash = metadata.get("source_hash")
    return SourceDoc(
        id=chunk_id, source_id=str(source_id),
        source_hash=source_hash if isinstance(source_hash, str) else None,
        type="github" if source_type == "github" else "docs", title=title,
        publisher="Knowledge base", url=url or "", relevance=0, excerpt=content[:6000], used=True,
        location=" · ".join(dict.fromkeys(label for label in labels if label)) or None,
    )


async def search_chunks(db: AsyncSession, query_text: str, limit: int = 5) -> list[SourceDoc]:
    terms = list(dict.fromkeys(term for term in SEARCH_TERMS.findall(query_text.casefold()) if term not in STOP_WORDS))
    if not terms:
        return []
    query = func.to_tsquery("simple", " | ".join(terms[:100]))
    rows = await db.execute(
        select(DocumentChunk, KnowledgeSource)
        .join(KnowledgeSource, KnowledgeSource.id == DocumentChunk.source_id)
        .where(
            DocumentChunk.search_text.op("@@")(query),
            KnowledgeSource.owner_id == owner_id(db),
            accessible_source(),
            KnowledgeSource.status.in_(("ready_for_embedding", "embedding", "indexed")),
        )
        .order_by(func.ts_rank(DocumentChunk.search_text, query).desc(), DocumentChunk.id)
        .limit(limit)
    )
    return [
        source_excerpt(
            chunk.chunk_id, source.id, chunk.content, chunk.meta, source.name, source.source_type, source.url,
        )
        for chunk, source in rows
    ]
