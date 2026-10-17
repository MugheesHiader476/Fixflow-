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


async def test_worker_persists_only_parsed_output(client: httpx.AsyncClient) -> None:
    identifier = await upload(client, (GOLDEN / "guide.md").read_bytes())
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source is not None and source.status == "parsed"
        result = await artifact_result(db, identifier)
        assert result.canonical.source.sha256 == source.file_hash
        assert source.ingestion_metadata["pipeline_contract_version"] == 3
        assert result.canonical.blocks and result.contract_version == 3
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        documents = list(await db.scalars(select(Document)))
        assert len(documents) == 1
        assert documents[0].page_content == "\n\n".join(b.content for b in result.canonical.blocks)


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
    assert (await client.get(f"/api/sources/{identifier}")).json()["status"] == "parsed"


async def test_incremental_update_keeps_document_identity_and_other_sources(client: httpx.AsyncClient) -> None:
    content = (GOLDEN / "guide.md").read_bytes()
    identifier = await upload(client, content)
    await ingest_source(identifier)
    other = await upload(client, b"# Other source\n\nOther documentation must remain untouched.")
    await ingest_source(other)
    async with get_session_factory()() as db:
        before = list(await db.scalars(select(Document).order_by(Document.id)))
        ids = [d.id for d in before]
        other_before = await artifact_result(db, other)
    changed = content.replace(b"Retry failed uploads", b"Reprocess failed uploads")
    assert await upload(client, changed, update_source_id=str(identifier)) == identifier
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert [d.id for d in await db.scalars(select(Document).order_by(Document.id))] == ids
        assert await artifact_result(db, other) == other_before
        assert (await artifact_result(db, identifier)).canonical.source.version == 2
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0


async def test_failed_update_keeps_previous_valid_artifacts_and_retry_recovers(client: httpx.AsyncClient) -> None:
    content = (GOLDEN / "guide.md").read_bytes()
    identifier = await upload(client, content)
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        before = await artifact_result(db, identifier)
        old_ids = set(await db.scalars(select(Document.id)))
    await upload(client, b"```python\ndef broken(\n```", update_source_id=str(identifier))
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert await artifact_result(db, identifier) == before
        assert set(await db.scalars(select(Document.id))) == old_ids
    await upload(client, content, update_source_id=str(identifier))
    await ingest_source(identifier)
    assert (await client.get(f"/api/sources/{identifier}")).json()["status"] == "parsed"


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
        assert (await artifact_result(db, identifier)).contract_version == 3


async def test_changed_private_file_is_rejected_before_persistence(client: httpx.AsyncClient) -> None:
    identifier = await upload(client, (GOLDEN / "guide.md").read_bytes())
    path = next(get_settings().upload_dir.glob("*/guide.md"))
    path.write_text("Content changed after upload and hashing.")
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert await db.get(IngestionArtifact, identifier) is None
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
