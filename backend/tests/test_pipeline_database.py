from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import Document, DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.schemas.pipeline import PipelineResult
from backend.services.ingestion import IngestionError, ingest_source

pytestmark = pytest.mark.anyio
GOLDEN = Path(__file__).parent / "fixtures/pipeline"


async def artifact_result(db: AsyncSession, identifier: UUID) -> PipelineResult:
    artifact = await db.get(IngestionArtifact, identifier)
    assert artifact is not None
    return PipelineResult.model_validate(artifact.result)


async def upload(client: httpx.AsyncClient, content: bytes, **options: str) -> UUID:
    response = await client.post("/api/documents", files={"file": ("guide.md", content)}, data=options)
    assert response.status_code == 202
    return UUID(response.json()["id"])


async def test_worker_persists_registration_canonical_okf_and_chunks(client: httpx.AsyncClient) -> None:
    identifier = await upload(client, (GOLDEN / "guide.md").read_bytes())
    async with get_session_factory()() as db:
        pending = await db.get(KnowledgeSource, identifier)
        assert pending is not None
        assert pending.ingestion_metadata["source_id"] == str(identifier)
        assert pending.ingestion_metadata["original_uri"] == f"source:{identifier}"
        assert pending.ingestion_metadata["mime_type"] == "text/markdown"
        assert pending.ingestion_metadata["version"] == 1
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        artifact = await db.get(IngestionArtifact, identifier)
        source = await db.get(KnowledgeSource, identifier)
        assert artifact is not None and source is not None
        result = await artifact_result(db, identifier)
        assert result.canonical.source.sha256 == source.file_hash
        registration = source.ingestion_metadata["registration"]
        assert isinstance(registration, dict) and registration["version"] == 1
        assert len(result.concepts) == 2
        assert "okf_version: '0.2'" in result.bundle["index.md"]
        records = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == identifier)))
        assert len(records) == 3
        assert all(r.meta["provenance"] and r.meta["source_block_ids"] for r in records)
        assert all(r.content == r.meta["raw_content"] for r in records)
        assert all(str(r.meta["retrieval_content"]).startswith("Concept:") for r in records)
        assert all(r.embedding is None for r in records)


async def test_identical_upload_reuses_processed_source_without_running_pipeline(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = (GOLDEN / "guide.md").read_bytes()
    identifier = await upload(client, content)
    await ingest_source(identifier)

    def forbidden(*args, **kwargs):
        raise AssertionError("Identical upload must reuse output")

    monkeypatch.setattr("backend.services.ingestion.run_pipeline", forbidden)
    assert await upload(client, content) == identifier
    await ingest_source(identifier)
    assert len(list(get_settings().upload_dir.glob("*/*"))) == 1
    assert (await client.get(f"/api/sources/{identifier}")).json()["status"] == "ready_for_embedding"


async def test_incremental_update_preserves_unaffected_rows_and_vectors(client: httpx.AsyncClient) -> None:
    content = (GOLDEN / "guide.md").read_bytes()
    identifier = await upload(client, content)
    await ingest_source(identifier)
    other = await upload(client, b"# Other source\n\nOther documentation must remain untouched.")
    await ingest_source(other)
    async with get_session_factory().begin() as db:
        records = list(
            await db.scalars(
                select(DocumentChunk).where(DocumentChunk.source_id == identifier).order_by(DocumentChunk.chunk_index)
            )
        )
        unchanged = records[0]
        unchanged.embedding, unchanged.embedding_model, unchanged.embedding_dimension = [1.0, 2.0, 3.0], "test-only", 3
        unchanged_id = unchanged.id
        obsolete_id = records[-1].id
        other_row = await db.scalar(select(DocumentChunk).where(DocumentChunk.source_id == other))
        assert other_row is not None
        other_id = other_row.id
    changed = content.replace(b"Retry failed uploads", b"Reprocess failed uploads")
    assert await upload(client, changed, update_source_id=str(identifier)) == identifier
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        record = await db.get(DocumentChunk, unchanged_id)
        assert record is not None and record.embedding is not None and list(record.embedding) == [1.0, 2.0, 3.0]
        assert await db.get(DocumentChunk, obsolete_id) is None
        assert await db.get(DocumentChunk, other_id) is not None
        result = await artifact_result(db, identifier)
        assert result.reused_concepts == 1
        assert result.canonical.source.version == 2
        assert await db.scalar(select(func.count()).select_from(Document)) == 3


async def test_failed_update_keeps_previous_valid_artifacts_and_retry_recovers(client: httpx.AsyncClient) -> None:
    content = (GOLDEN / "guide.md").read_bytes()
    identifier = await upload(client, content)
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        before = await artifact_result(db, identifier)
        old_ids = set(await db.scalars(select(DocumentChunk.id)))
    await upload(client, b"# Empty updated source", update_source_id=str(identifier))
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert await artifact_result(db, identifier) == before
        assert set(await db.scalars(select(DocumentChunk.id))) == old_ids
    await upload(client, content, update_source_id=str(identifier))
    await ingest_source(identifier)
    assert (await client.get(f"/api/sources/{identifier}")).json()["status"] == "ready_for_embedding"


async def test_source_update_authorization_and_duplicate_guard(client: httpx.AsyncClient) -> None:
    identifier = await upload(client, (GOLDEN / "guide.md").read_bytes())
    response = await client.post(
        "/api/documents",
        data={"update_source_id": str(identifier), "content": "A different user's untrusted update."},
        headers={"X-FixFlow-User-Id": "user_other"},
    )
    assert response.status_code == 404
    other = await upload(client, b"A second source with independent evidence.")
    response = await client.post(
        "/api/documents",
        data={"update_source_id": str(identifier), "content": "A second source with independent evidence."},
    )
    assert response.status_code == 409
    assert other != identifier
    assert len(list(get_settings().upload_dir.glob("*/*"))) == 2


async def test_force_option_requeues_valid_source(client: httpx.AsyncClient) -> None:
    content = (GOLDEN / "guide.md").read_bytes()
    identifier = await upload(client, content)
    await ingest_source(identifier)
    assert await upload(client, content, force="true") == identifier
    assert (await client.get(f"/api/sources/{identifier}")).json()["status"] == "uploaded"
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert (await artifact_result(db, identifier)).reused_concepts == 0


async def test_changed_private_file_is_rejected_before_persistence(client: httpx.AsyncClient) -> None:
    identifier = await upload(client, (GOLDEN / "guide.md").read_bytes())
    path = next(get_settings().upload_dir.glob("*/guide.md"))
    path.write_text("Content changed after upload and hashing.")
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert await db.get(IngestionArtifact, identifier) is None
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
