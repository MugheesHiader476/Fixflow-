"""Historical search/vector storage survives the additive parsing-only migration."""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.db.session import close_database, get_session_factory
from backend.tests.legacy_records import content_hash


async def migrate(revision: str) -> None:
    await close_database()
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "downgrade" if revision == "0004" else "upgrade", revision],
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        check=True,
        capture_output=True,
        timeout=60,
    )


async def snapshot() -> list[list[object]]:
    async with get_session_factory()() as db:
        return [
            list(await db.scalars(text(f"SELECT to_jsonb(entry) - 'search_text' FROM {table} entry ORDER BY entry.id")))
            for table in ["knowledge_sources", "documents", "document_chunks"]
        ]


@pytest.mark.anyio
async def test_migrations_preserve_existing_rows_and_vectors(client: httpx.AsyncClient) -> None:
    await migrate("0004")
    try:
        async with get_session_factory().begin() as db:
            body = "Legacy evidence remains preserved."
            source = KnowledgeSource(
                owner_id="user_test", name="Legacy", source_type="docs", file_hash=content_hash(body), status="indexed"
            )
            db.add(source)
            await db.flush()
            document = Document(
                id=uuid4(),
                source_id=source.id,
                document_index=0,
                page_content=body,
                meta={},
                content_hash=content_hash(body),
            )
            db.add(document)
            await db.flush()
            db.add(
                DocumentChunk(
                    id=uuid4(),
                    document_id=document.id,
                    source_id=source.id,
                    chunk_id="legacy-" + str(uuid4()),
                    chunk_index=0,
                    content=body,
                    content_hash=content_hash(body),
                    meta={"retrieval_content": "Context " + body},
                    embedding=[1.0, 2.0, 3.0],
                    embedding_model="historical-model",
                    embedding_dimension=3,
                )
            )
        before = await snapshot()
        await migrate("head")
        assert await snapshot() == before
        response = await client.get("/api/sources")
        assert response.status_code == 200
        assert response.json()[0]["retrieval_available"] is False
        assert response.json()[0]["chunk_count"] == 1
        assert (await client.post("/api/debug", json={"error": "Legacy evidence"})).status_code == 410
        await migrate("0004")
        assert await snapshot() == before
    finally:
        await migrate("head")
