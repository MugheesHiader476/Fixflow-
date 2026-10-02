"""Shared lexical retrieval; no model calls or fabricated similarity scores."""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import DocumentChunk, KnowledgeSource
from backend.repositories.source_access import accessible_source
from backend.schemas.models import SourceDoc
from backend.services.access import owner_id

SEARCH_TERMS = re.compile(r"[^\W_]{3,}", re.UNICODE)
STOP_WORDS = {"the", "and", "for", "with", "this", "that", "how", "does", "what", "from", "can", "you"}


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
        SourceDoc(
            id=chunk.chunk_id,
            type="github" if source.source_type == "github" else "docs",
            title=source.name,
            publisher="Knowledge base",
            url=source.url or "",
            relevance=0,
            excerpt=chunk.content[:6000],
            used=True,
        )
        for chunk, source in rows
    ]
