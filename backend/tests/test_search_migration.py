"""Search projection upgrade preserves rows, legacy retrieval and account isolation."""

import asyncio
import os
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.db.session import close_database, get_session_factory
from backend.repositories.corpus import content_hash
from backend.services.ingestion import ingest_source


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
            list(await db.scalars(text(
                f"SELECT to_jsonb(entry) - 'search_text' FROM {table} entry "
                f"ORDER BY entry.{'source_id' if table == 'ingestion_artifacts' else 'id'}"
            )))
            for table in ["knowledge_sources", "documents", "document_chunks", "ingestion_artifacts"]
        ]


@pytest.mark.anyio
async def test_search_context_migration_reindexes_existing_rows_without_data_changes(client: httpx.AsyncClient) -> None:
    await migrate("0004")
    try:
        accepted = await client.post(
            "/api/documents",
            files={"file": ("migration.md", b"# ZXQMIGRATIONHEADING47391\n\nZXQMIGRATIONBODY47391 complete evidence.")},
        )
        assert accepted.status_code == 202
        await ingest_source(UUID(accepted.json()["id"]))
        async with get_session_factory()() as db:
            body = "ZXQLEGACYBODY47391 legacy evidence remains searchable."
            source = KnowledgeSource(
                owner_id="user_test", name="Legacy", source_type="docs", file_hash=content_hash(body),
                status="ready_for_embedding",
            )
            db.add(source)
            await db.flush()
            document = Document(
                id=uuid4(), source_id=source.id, document_index=0, page_content=body, meta={},
                content_hash=content_hash(body),
            )
            db.add(document)
            await db.flush()
            db.add(
                DocumentChunk(
                    id=uuid4(), document_id=document.id, source_id=source.id, chunk_id="legacy-" + str(uuid4()),
                    chunk_index=0, content=body, content_hash=content_hash(body),
                    meta={"retrieval_content": "Unrelated invalid context must not replace actual legacy content"},
                )
            )
            await db.commit()
        before = await snapshot()
        assert not (await client.post("/api/debug", json={"error": "ZXQMIGRATIONHEADING47391"})).json()["sources"]
        await migrate("head")
        assert await snapshot() == before
        for query in ["ZXQMIGRATIONHEADING47391", "ZXQMIGRATIONBODY47391", "ZXQLEGACYBODY47391"]:
            found = await client.post("/api/debug", json={"error": query})
            assert found.status_code == 200 and found.json()["sources"]
            foreign = await client.post(
                "/api/debug", json={"error": query}, headers={"X-FixFlow-User-Id": "user_other"}
            )
            assert foreign.status_code == 200 and foreign.json()["sources"] == []
        await migrate("0004")
        assert await snapshot() == before
        assert not (await client.post("/api/debug", json={"error": "ZXQMIGRATIONHEADING47391"})).json()["sources"]
    finally:
        await migrate("head")
