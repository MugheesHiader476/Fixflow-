from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.db.session import get_engine
from backend.repositories.sources import delete_source, list_sources
from backend.services import ingestion
from backend.tests.legacy_records import chunk_values, content_hash, document_values, insert_chunks, insert_documents

pytestmark = pytest.mark.anyio


async def make_corpus(db: AsyncSession, name: str = "guide.md") -> tuple[UUID, UUID, UUID]:
    source = KnowledgeSource(name=name, source_type="docs", file_hash=content_hash(name), status="ready_for_embedding")
    db.add(source)
    await db.flush()
    document = document_values(source.id, "Important documentation content.", {"title": "Guide", "page": 2}, 0)
    document_id = UUID(str(document["id"]))
    assert await insert_documents(db, [document]) == 1
    chunk = chunk_values(source.id, document_id, "Important documentation content.", {"section": "start"}, 0)
    assert await insert_chunks(db, [chunk]) == 1
    await db.commit()
    return source.id, document_id, UUID(str(chunk["id"]))


async def test_connection_pgvector_and_migrations(db: AsyncSession) -> None:
    assert await db.scalar(text("SELECT 1")) == 1
    assert await db.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
    assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "0010"
    assert await db.scalar(text("SELECT vector_dims('[1,0,0]'::vector)")) == 3
    assert get_engine() is get_engine()


async def test_source_documents_chunks_dedup_and_cascade(db: AsyncSession) -> None:
    source_id, document_id, chunk_id = await make_corpus(db)
    assert await insert_documents(db, [document_values(source_id, "Important documentation content.", {}, 1)]) == 0
    assert (
        await insert_chunks(db, [chunk_values(source_id, document_id, "Important documentation content.", {}, 2)]) == 0
    )
    sources = await list_sources(db)
    assert sources[0].document_count == sources[0].chunk_count == 1
    document = await db.get(Document, document_id)
    chunk = await db.get(DocumentChunk, chunk_id)
    assert document and document.meta == {"title": "Guide", "page": 2}
    assert chunk and chunk.embedding is chunk.embedding_dimension is chunk.embedding_model is None
    assert await delete_source(db, source_id)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Document)) == 0
    assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0


async def test_chunk_cannot_reference_another_sources_document(db: AsyncSession) -> None:
    _, document_id, _ = await make_corpus(db)
    other_id, _, _ = await make_corpus(db, "other.md")
    with pytest.raises(IntegrityError):
        await insert_chunks(db, [chunk_values(other_id, document_id, "Invalid cross-source reference", {}, 9)])
    await db.rollback()


async def test_partial_ingestion_rolls_back_then_retries(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = await client.post("/api/documents", files={"file": ("retry.md", b"Original document text")})
    source_id = UUID(response.json()["source_id"])
    real_persistence = ingestion.persist_result

    async def failed_persistence(session, identifier, result):
        await real_persistence(session, identifier, result)
        await session.flush()
        raise RuntimeError("sensitive-driver-error")

    monkeypatch.setattr(ingestion, "persist_result", failed_persistence)
    monkeypatch.setattr(get_settings(), "ingestion_batch_size", 1)
    with pytest.raises(ingestion.IngestionError):
        await ingestion.ingest_source(source_id)
    detail = (await client.get(f"/api/sources/{source_id}")).json()
    assert detail["status"] == "failed"
    assert detail["document_count"] == detail["chunk_count"] == 0
    assert "sensitive" not in detail["error_message"]
    monkeypatch.setattr(ingestion, "persist_result", real_persistence)
    # A replacement upload must also recover a lost original file.
    original = next(get_settings().upload_dir.glob("*/retry.md"))
    original.unlink()
    retry = await client.post("/api/documents", files={"file": ("retry.md", b"Original document text")})
    assert retry.json()["source_id"] == str(source_id)
    assert retry.json()["status"] == "uploaded"
    await ingestion.ingest_source(source_id)
    assert (await client.get(f"/api/sources/{source_id}")).json()["status"] == "parsed"


async def test_only_new_file_is_loaded_and_workers_deduplicate(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    real_runner = ingestion.run_pipeline

    def tracked_runner(path: Path, identifier: str, config, **kwargs):
        calls.append(path.name)
        return real_runner(path, identifier, config, **kwargs)

    monkeypatch.setattr(ingestion, "run_pipeline", tracked_runner)
    for name in ("first.md", "second.md"):
        response = await client.post(
            "/api/documents",
            files={
                "file": (
                    name,
                    f"# Documentation for {name}\n\nThis document explains a useful recovery procedure.".encode(),
                )
            },
        )
        identifier = UUID(response.json()["id"])
        await asyncio.gather(ingestion.ingest_source(identifier), ingestion.ingest_source(identifier))
        await ingestion.ingest_source(identifier)
    assert calls == ["first.md", "second.md"]
    assert all(source["chunk_count"] == 0 for source in (await client.get("/api/sources")).json())


async def test_missing_upload_is_failed_without_reading_other_paths(db: AsyncSession, tmp_path: Path) -> None:
    source = KnowledgeSource(name="missing.pdf", source_type="upload", file_hash="a" * 64, path="/etc/passwd")
    db.add(source)
    await db.commit()
    with pytest.raises(ingestion.IngestionError):
        await ingestion.ingest_source(source.id)
    await db.refresh(source)
    assert source.status == "failed"
    assert "unavailable" in (source.error_message or "")


@pytest.mark.parametrize("has_text", [True, False])
async def test_real_pdf_loading_and_empty_pdf_failure(client: httpx.AsyncClient, has_text: bool) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    if has_text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 20 200 Td (A PDF with meaningful documentation for the database ingestion test.) Tj ET"
        )
        page[NameObject("/Contents")] = stream
    buffer = BytesIO()
    writer.write(buffer)
    writer.close()
    response = await client.post("/api/documents", files={"file": ("test.pdf", buffer.getvalue(), "application/pdf")})
    source_id = UUID(response.json()["source_id"])
    if has_text:
        await ingestion.ingest_source(source_id)
    else:
        with pytest.raises(ingestion.IngestionError):
            await ingestion.ingest_source(source_id)
    detail = (await client.get(f"/api/sources/{source_id}")).json()
    assert detail["status"] == ("parsed" if has_text else "failed")
    assert detail["document_count"] == int(has_text)
    assert detail["chunk_count"] == 0
