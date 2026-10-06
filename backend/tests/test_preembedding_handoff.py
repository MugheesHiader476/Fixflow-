"""Prepared-source release gate checks against actual disposable SQL projections."""

from email.message import EmailMessage
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select

from backend.db.models import DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.runner import run_pipeline
from backend.repositories.prepared import prepared_source
from backend.schemas.pipeline import PipelineResult
from backend.services.ingestion import ingest_source


@pytest.mark.anyio
@pytest.mark.parametrize(
    "damage",
    ["continuation", "provenance", "authorization", "legacy", "missing_chunk", "inactive", "processing", "failed"],
)
async def test_prepared_handoff_rejects_each_unvalidated_or_unavailable_case(
    client: httpx.AsyncClient, damage: str
) -> None:
    accepted = await client.post(
        "/api/documents",
        files={"file": ("release.xml", b"<article>" + b"authorized evidence " * 600 + b"</article>")},
    )
    assert accepted.status_code == 202
    identifier = UUID(accepted.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        result = await prepared_source(db, identifier, "user_test")
        assert result and result.coverage["continuation_reconstruction_percent"] == 100
    async with get_session_factory().begin() as db:
        artifact = await db.get(IngestionArtifact, identifier)
        source = await db.get(KnowledgeSource, identifier)
        assert artifact and source
        if damage in {"inactive", "processing", "failed"}:
            if damage == "inactive":
                source.is_active = False
            else:
                source.status = damage
            artifact.result = {}  # SQL authorization/status filter must precede decoding private data.
        elif damage == "missing_chunk":
            chunk = await db.scalar(select(DocumentChunk).where(DocumentChunk.source_id == identifier))
            assert chunk
            await db.execute(delete(DocumentChunk).where(DocumentChunk.id == chunk.id))
        else:
            data = PipelineResult.model_validate(artifact.result)
            if damage == "continuation":
                continuation = data.chunks[1].unit_slices[0].continuation
                assert continuation
                continuation.count += 1
            elif damage == "provenance":
                data.chunks[0].provenance[0].source_id = "foreign-source"
            elif damage == "authorization":
                data.chunks[0].authorization_scope = "source:foreign"
            else:
                data.contract_version = 1
            artifact.result = data.model_dump(mode="json")
    async with get_session_factory()() as db:
        assert await prepared_source(db, identifier, "user_other") is None
        if damage in {"inactive", "processing", "failed"}:
            assert await prepared_source(db, identifier, "user_test") is None
        else:
            with pytest.raises(ValueError):
                await prepared_source(db, identifier, "user_test")


def test_uploaded_email_preserves_ordered_headers_as_nonretrieval_metadata(tmp_path: Path) -> None:
    message = EmailMessage()
    message["From"] = "Alice <alice@example.test>"
    message["To"] = "Bob <bob@example.test>"
    message["Date"] = "Tue, 06 Oct 2026 10:00:00 +0000"
    message["Subject"] = "Preserved email"
    message["X-Reference"] = "first"
    message["X-Reference"] = "second"
    message.set_content("MAILBEGIN73129 meaningful body. MAILEND73129 final exception.")
    path = tmp_path / "message.eml"
    path.write_bytes(message.as_bytes())
    result = run_pipeline(path, "email-provenance")
    headers = result.canonical.metadata.get("email_headers")
    assert headers == [{"name": name, "value": str(value)} for name, value in message.items()]
    assert result.canonical.metadata["nonretrieval_metadata"] == {
        "email_headers": "Original MIME headers retained for provenance"
    }
    assert "MAILBEGIN73129" in "\n".join(c.raw_content for c in result.chunks)
    assert result.coverage["coverage_percent"] == 100
