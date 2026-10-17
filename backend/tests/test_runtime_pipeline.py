from __future__ import annotations

import os
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text

from backend.config import get_settings
from backend.db.models import DocumentChunk, KnowledgeSource
from backend.db.session import close_database, get_session_factory
from backend.main import app
from backend.repositories.parsed import parsed_source
from backend.services.ingestion import IngestionError, ingest_source
from scripts.assign_legacy_owner import assign_legacy_owner

pytestmark = pytest.mark.anyio


async def test_gateway_rejects_missing_wrong_and_spoofed_identity(client: httpx.AsyncClient) -> None:
    for headers in (
        {},
        {"X-FixFlow-User-Id": "user_test"},
        {"Authorization": "Bearer wrong", "X-FixFlow-User-Id": "user_test"},
        {"Authorization": client.headers["Authorization"], "X-FixFlow-User-Id": "__legacy__"},
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
        ) as anonymous:
            assert (await anonymous.get("/api/sources")).status_code == 401
            assert (await anonymous.post("/api/documents", data={"content": "Hello"})).status_code == 401


async def test_authentication_fails_closed_when_unconfigured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "fixflow_api_token", None)
    assert (await client.get("/api/sessions")).status_code == 503


async def test_users_cannot_read_or_deduplicate_each_others_sources(client: httpx.AsyncClient) -> None:
    content = "# Private\nPrivateaccountmarker documentation for confidential account recovery."
    first = (await client.post("/api/documents", data={"content": content})).json()
    await ingest_source(UUID(first["id"]))
    saved = await client.post("/api/saved", json={"problem": "Privateaccountmarker", "rootCause": "", "fixSummary": ""})
    assert saved.status_code == 200
    other = {"X-FixFlow-User-Id": "user_other"}
    for path in ("/api/sources", "/api/sessions", "/api/saved"):
        assert (await client.get(path, headers=other)).json() == []
    assert (await client.get(f"/api/sources/{first['id']}", headers=other)).status_code == 404
    second = (await client.post("/api/documents", headers=other, data={"content": content})).json()
    assert second["id"] != first["id"]
    await ingest_source(UUID(second["id"]))
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        assert await parsed_source(db, UUID(first["id"]), "user_other") is None
        assert await parsed_source(db, UUID(first["id"]), "user_test") is not None
    assert (await client.get("/api/readiness", headers=other)).json()["sources"] == 1
    assert (await client.get("/health")).json()["sources"] is None


@pytest.mark.parametrize(
    "name,content",
    [
        ("a.md", b"\x00binary"),
        ("a.txt", b"\xff"),
        ("a.md", b"   \n"),
        ("a.pdf", b"pretend pdf"),
        ("a.docx", b"pretend docx"),
    ],
)
async def test_invalid_document_content_never_creates_a_source(
    client: httpx.AsyncClient, name: str, content: bytes
) -> None:
    response = await client.post("/api/documents", files={"file": (name, content)})
    assert response.status_code == 422
    assert (await client.get("/api/sources")).json() == []
    assert not list(get_settings().upload_dir.glob("*/*"))


async def test_upload_rejects_ambiguous_or_invalid_format(client: httpx.AsyncClient) -> None:
    assert (
        await client.post("/api/documents", data={"ingestion_format": "unknown", "content": "Text"})
    ).status_code == 422
    assert (
        await client.post("/api/documents", data={"content": "Text"}, files={"file": ("a.txt", b"Text")})
    ).status_code == 422
    assert (
        await client.post("/api/documents", data={"ingestion_format": "okf"}, files={"file": ("a.txt", b"Text")})
    ).status_code == 422


async def test_strict_okf_failure_is_persisted_without_plaintext_fallback(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/documents", data={"ingestion_format": "okf", "content": "---\ntitle: Missing type\n---\nBody"}
        )
    ).json()
    with pytest.raises(IngestionError, match="Invalid OKF"):
        await ingest_source(UUID(source["id"]))
    failed = (await client.get(f"/api/sources/{source['id']}")).json()
    assert failed["status"] == "failed"
    assert failed["chunk_count"] == failed["document_count"] == 0
    assert failed["ingestion_format"] == "okf"


async def test_strict_okf_body_and_metadata_roundtrip(client: httpx.AsyncClient) -> None:
    content = "---\ntype: Reference\ntitle: Runtime validation\ncustom: retained\n---\n# Recovery\nRecover safely."
    source = (await client.post("/api/documents", data={"ingestion_format": "okf", "content": content})).json()
    await ingest_source(UUID(source["id"]))
    async with get_session_factory()() as db:
        result = await parsed_source(db, UUID(source["id"]), "user_test")
        assert result is not None
        metadata = result.canonical.metadata["uploaded_okf"]
        assert isinstance(metadata, dict) and metadata["custom"] == "retained"
        assert all("custom" not in block.content for block in result.canonical.blocks)
    assert (await client.get(f"/api/sources/{source['id']}")).json()["embedding_status"] == "not_configured"


async def test_extraction_limit_rolls_back_all_chunks(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_extracted_chars", 10)
    source = (
        await client.post(
            "/api/documents", data={"content": "An input exceeding the configured text processing limit."}
        )
    ).json()
    with pytest.raises(IngestionError):
        await ingest_source(UUID(source["id"]))
    assert (await client.get(f"/api/sources/{source['id']}")).json()["chunk_count"] == 0


async def test_request_guard_bounds_declared_and_streamed_bodies(client: httpx.AsyncClient) -> None:
    oversized = await client.post("/api/debug", headers={"Content-Length": str(9 * 1024 * 1024)}, content=b"{}")
    assert oversized.status_code == 413

    async def chunks():
        for _ in range(9):
            yield b"x" * (1024 * 1024)

    streamed = await client.post("/api/debug", content=chunks())
    assert streamed.status_code == 413
    assert (await client.get("/api/sessions")).json() == []


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/debug", {"error": "Valid problem", "techs": ["bad\x00technology"]}),
        ("/api/debug", {"error": "Valid problem", "technology": "bad\x00technology"}),
        ("/api/debug", {"error": "Valid problem", "repo_url": "https://example.test/\x00"}),
        ("/api/debug", {"error": "invalid\ud800unicode"}),
        (
            "/api/saved",
            {
                "problem": "Valid problem",
                "rootCause": "",
                "fixSummary": "",
                "sources": [{"title": "bad\x00title", "type": "docs"}],
            },
        ),
    ],
)
async def test_json_text_is_validated_before_postgresql(
    client: httpx.AsyncClient, path: str, payload: dict[str, object]
) -> None:
    import json  # noqa: PLC0415

    response = await client.post(path, content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert (await client.get("/api/sessions")).json() == []
    assert (await client.get("/api/saved")).json() == []


async def test_migration_preserves_preexisting_legacy_data(database: None) -> None:
    environment = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    await close_database()
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0001"], env=environment, check=True, timeout=60)
    identifier = uuid4()
    async with get_session_factory().begin() as db:
        await db.execute(
            text(
                "INSERT INTO knowledge_sources (id,name,source_type,file_hash,status) "
                "VALUES (:id,'preserved.md','docs',:hash,'ready_for_embedding')"
            ),
            {"id": identifier, "hash": "a" * 64},
        )
    await close_database()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True, timeout=60)
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source is not None
        assert source.owner_id == "__legacy__"
        assert source.name == "preserved.md"
        assert source.file_hash == "a" * 64
        assert source.status == "ready_for_embedding"


async def test_legacy_assignment_requires_explicit_apply_and_preserves_records(client: httpx.AsyncClient) -> None:
    identifier = uuid4()
    async with get_session_factory().begin() as db:
        db.add(KnowledgeSource(id=identifier, name="preserved.md", source_type="docs", file_hash="b" * 64))
    assert (await client.get("/api/sources")).json() == []
    preview = await assign_legacy_owner("user_test")
    assert preview["knowledge_sources"] == 1
    assert (await client.get("/api/sources")).json() == []
    await assign_legacy_owner("user_test", apply=True)
    assert (await client.get("/api/sources")).json()[0]["id"] == str(identifier)


async def test_legacy_assignment_conflict_rolls_back(client: httpx.AsyncClient) -> None:
    async with get_session_factory().begin() as db:
        for owner in ("__legacy__", "user_test"):
            db.add(KnowledgeSource(owner_id=owner, name="same.md", source_type="docs", file_hash="c" * 64))
    with pytest.raises(ValueError, match="duplicate"):
        await assign_legacy_owner("user_test", apply=True)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == 2
