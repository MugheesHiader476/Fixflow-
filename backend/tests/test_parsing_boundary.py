"""Parsing remains private and validated after all downstream processing is removed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from backend.db.models import Document, DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.runner import run_pipeline
from backend.processing.pipeline.validation import validate_result
from backend.repositories.parsed import parsed_source
from backend.services.ingestion import IngestionError, ingest_source

FIXTURES = Path(__file__).parent / "fixtures/pipeline"


@pytest.mark.parametrize("damage", ["content", "hash", "provenance", "context", "identity", "stage", "version"])
def test_parsing_gate_rejects_damaged_canonical_output(damage: str) -> None:
    result = run_pipeline(FIXTURES / "guide.md", "damage")
    if damage == "content":
        result.canonical.blocks[-1].content += "invented"
    elif damage == "hash":
        result.canonical.content_hash = "0" * 64
    elif damage == "provenance":
        result.canonical.blocks[0].provenance.source_id = "foreign"
    elif damage == "context":
        result.canonical.source.context["authorization_scope"] = "source:foreign"
    elif damage == "identity":
        result.canonical.document_id = "foreign"
    elif damage == "stage":
        result.stage_hashes["canonical"] = "0" * 64
    else:
        result.engine_version = "old"
    with pytest.raises(ValueError):
        validate_result(result)


def test_email_headers_still_preserve_order_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "headers.eml"
    path.write_text(
        "Received: first\nReceived: second\nSubject: Header preservation\nFrom: a@example.test\n\nBody evidence."
    )
    result = run_pipeline(path, "email")
    headers = result.canonical.metadata["email_headers"]
    assert isinstance(headers, list)
    assert headers[0] == {"name": "Received", "value": "first"}
    assert headers[1] == {"name": "Received", "value": "second"}
    nonretrieval = result.canonical.metadata["nonretrieval_metadata"]
    assert isinstance(nonretrieval, dict) and "email_headers" in nonretrieval
    assert any("Body evidence" in block.content for block in result.canonical.blocks)


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["inactive", "expired", "processing", "failed", "legacy", "foreign"])
async def test_unavailable_sources_are_excluded_before_artifact_decoding(client: httpx.AsyncClient, state: str) -> None:
    accepted = await client.post("/api/documents", data={"content": "Private parsing boundary evidence."})
    identifier = UUID(accepted.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        artifact = await db.get(IngestionArtifact, identifier)
        assert source and artifact
        artifact.result = {"deliberately": "invalid"}
        if state == "inactive":
            source.is_active = False
        elif state == "expired":
            source.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state in {"processing", "failed"}:
            source.status = state
        elif state == "legacy":
            source.status = "ready_for_embedding"
        await db.flush()
        assert await parsed_source(db, identifier, "user_other" if state == "foreign" else "user_test") is None


@pytest.mark.anyio
async def test_failed_validation_rolls_back_projections_and_retains_raw_file(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepted = await client.post("/api/documents", data={"content": "Raw parsing evidence must remain private."})
    identifier = UUID(accepted.json()["id"])

    def damaged(path, source_id, config, **kwargs):
        result = run_pipeline(path, source_id, config, **kwargs)
        result.canonical.blocks[0].content += "corruption"
        return result

    monkeypatch.setattr("backend.services.ingestion.run_pipeline", damaged)
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source and source.status == "failed" and source.path and Path(source.path).is_file()
        assert await db.get(IngestionArtifact, identifier) is None
        assert await db.scalar(select(func.count()).select_from(Document)) == 0
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0


@pytest.mark.anyio
async def test_registered_owner_and_hash_are_verified_on_read(client: httpx.AsyncClient) -> None:
    accepted = await client.post("/api/documents", data={"content": "Canonical output is registered and owner scoped."})
    identifier = UUID(accepted.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source
        source.file_hash = "0" * 64
        await db.flush()
        with pytest.raises(ValueError, match="registration"):
            await parsed_source(db, identifier, "user_test")


def test_retired_inference_environment_cannot_enable_features(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config import Settings  # noqa: PLC0415

    monkeypatch.setenv("EMBEDDING_API_URL", "https://unused.example.test")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("GENERATION_MODEL", "retired-model")
    settings = Settings()
    assert not {"embedding_api_url", "ollama_url", "generation_model"}.intersection(type(settings).model_fields)


@pytest.mark.anyio
async def test_explicit_reupload_upgrades_legacy_artifact_without_erasing_legacy_vectors(
    client: httpx.AsyncClient,
) -> None:
    from uuid import uuid4  # noqa: PLC0415

    body = "Legacy source content is explicitly reparsed with its identity preserved."
    accepted = await client.post("/api/documents", data={"content": body})
    identifier = UUID(accepted.json()["id"])
    await ingest_source(identifier)
    legacy_id = uuid4()
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        artifact = await db.get(IngestionArtifact, identifier)
        document = await db.scalar(select(Document).where(Document.source_id == identifier))
        assert source and artifact and document
        payload = artifact.result.copy()
        canonical = payload["canonical"]
        assert isinstance(canonical, dict)
        payload["canonical"] = {**canonical, "version": "2.1.2"}
        payload.update(contract_version=2, engine_version="2.1.2", concepts=[], chunks=[], bundle={}, parents=[])
        artifact.result = payload
        source.status = "indexed"
        source.ingestion_metadata = {**source.ingestion_metadata, "pipeline_contract_version": 2}
        db.add(
            DocumentChunk(
                id=legacy_id,
                document_id=document.id,
                source_id=identifier,
                chunk_id="preserved-legacy-vector",
                chunk_index=0,
                content=body,
                content_hash="a" * 64,
                meta={"historical": True},
                embedding=[1.0, 2.0, 3.0],
                embedding_model="historical-model",
                embedding_dimension=3,
            )
        )
    repeated = await client.post("/api/documents", data={"content": body})
    assert repeated.status_code == 202 and repeated.json()["id"] == str(identifier)
    assert repeated.json()["status"] == "uploaded"
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        result = await parsed_source(db, identifier, "user_test")
        assert result and result.engine_version == "3.0.0" and result.contract_version == 3
        assert result.canonical.source.version == 1
        legacy = await db.get(DocumentChunk, legacy_id)
        assert legacy and legacy.embedding is not None
        assert list(legacy.embedding) == [1.0, 2.0, 3.0]
        assert legacy.meta == {"historical": True}
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 1
