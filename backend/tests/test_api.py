from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text

from backend.config import get_settings
from backend.db.models import DocumentChunk
from backend.db.session import close_database, get_session_factory
from backend.main import app
from backend.schemas.models import ChatMessage, DebugRequest, Diagnosis, DiagnosisDraft, SourceDoc
from backend.services.diagnosis import DocumentationProvider, get_diagnosis_provider
from backend.services.ingestion import ingest_source

pytestmark = pytest.mark.anyio


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/readiness")
    assert response.status_code == 200
    health = response.json()
    assert health["status"] == health["api"] == "ok"
    assert health["database"] == "connected"
    assert health["pgvector"] == "available"
    assert health["schema"] == "ready"
    assert health["revision"] == health["expected_revision"] == "0004"
    assert health["sources"] == health["documents"] == health["chunks"] == health["embedded_chunks"] == 0
    assert health["ai_generation"] == "not_configured"


async def test_upload_ingestion_status_and_persistence(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/documents",
        files={
            "file": ("notes.md", b"# Notes\nThe asyncio event loop schedules coroutine callbacks.", "text/markdown"),
        },
    )
    assert response.status_code == 202
    source_id = response.json()["source_id"]
    assert response.json()["id"] == source_id
    assert response.json()["status"] == "uploaded"
    await ingest_source(UUID(source_id))
    await close_database()
    detail = (await client.get(f"/api/sources/{source_id}")).json()
    assert detail["status"] == "ready_for_embedding"
    assert detail["document_count"] == detail["chunk_count"] == 1
    assert (await client.get(f"/api/sources/{source_id}/status")).json() == detail
    assert (await client.get("/api/sources")).json() == [detail]
    async with get_session_factory()() as session:
        assert (await session.scalars(select(DocumentChunk))).one().embedding is None


async def test_deduplicate_concurrent_uploads(client: httpx.AsyncClient) -> None:
    responses = await asyncio.gather(
        *(
            client.post(
                "/api/documents",
                files={
                    "file": (f"notes-{index}.md", b"# Same document", "text/markdown"),
                },
            )
            for index in range(6)
        )
    )
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["source_id"] for response in responses}) == 1
    assert len((await client.get("/api/sources")).json()) == 1
    assert len(list(get_settings().upload_dir.glob("*/*.md"))) == 1


async def test_debug_chat_and_saved_sessions_persist(client: httpx.AsyncClient) -> None:
    upload = await client.post(
        "/api/documents",
        data={
            "kind": "docs",
            "value": "asyncio.md",
            "content": "The asyncio event loop schedules coroutine callbacks.",
        },
    )
    await ingest_source(UUID(upload.json()["source_id"]))
    diagnosis = (await client.post("/api/debug", json={"error": "RuntimeError: no running event loop"})).json()
    assert diagnosis["sources"][0]["title"] == "asyncio.md"
    assert diagnosis["generation"] == "disabled"
    assert diagnosis["confidence"] is diagnosis["codeFix"] is None
    reply = await client.post(
        "/api/chat",
        json={
            "session_id": diagnosis["sessionId"],
            "question": "How does the asyncio event loop work?",
        },
    )
    assert reply.status_code == 200
    assert "asyncio.md" in reply.json()["text"]
    saved = await client.post(
        "/api/saved",
        json={
            "problem": "Failure",
            "rootCause": "Cause",
            "technology": ["Python"],
            "fixSummary": "Fix",
            "sources": [],
        },
    )
    assert saved.status_code == 200
    await close_database()
    assert (await client.get(f"/api/sessions/{diagnosis['sessionId']}")).json() == diagnosis
    messages = (await client.get(f"/api/sessions/{diagnosis['sessionId']}/messages")).json()
    assert [message["role"] for message in messages] == ["user", "fixflow"]
    assert messages[1] == reply.json()
    assert len((await client.get("/api/sessions")).json()) == 1
    assert (await client.get("/api/saved")).json() == [saved.json()]


async def test_invalid_requests_use_safe_errors(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/debug", json={})).status_code == 422
    assert (await client.post("/api/debug", json={"context": "   "})).status_code == 422
    assert (await client.post("/api/chat", json={"session_id": "missing", "question": "hello"})).status_code == 404
    assert (await client.post("/api/chat", json={"session_id": "x", "question": " "})).status_code == 422
    assert (await client.get(f"/api/sources/{uuid4()}")).status_code == 404
    assert (await client.get("/api/sources/not-uuid")).status_code == 422


async def test_debug_uses_code_as_diagnostic_context(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/debug",
        json={"code": "asyncio.get_running_loop()  # RuntimeError: no running event loop"},
    )

    assert response.status_code == 200
    diagnosis = response.json()
    assert diagnosis["status"] == "no-cause"
    assert diagnosis["generation"] == "disabled"
    assert diagnosis["confidence"] is None
    assert "event loop" in diagnosis["rag"]["query"]


async def test_upload_validation(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await client.post("/api/documents", files={"file": ("payload.exe", b"bad")})).status_code == 415
    assert (await client.post("/api/documents", files={"file": ("empty.md", b"")})).status_code == 422
    assert (await client.post("/api/documents", data={"kind": "bad"})).status_code == 422
    assert (await client.post("/api/documents")).status_code == 400
    monkeypatch.setattr("backend.services.uploads.MAX_UPLOAD_BYTES", 10)
    assert (await client.post("/api/documents", files={"file": ("notes.md", b"x" * 11)})).status_code == 413


async def test_filename_permissions_and_long_names(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/documents", files={"file": ("../../private.md", b"private content")})
    assert response.status_code == 202
    assert response.json()["name"] == "private.md"
    stored = next(get_settings().upload_dir.glob("*/private.md"))
    assert stored.stat().st_mode & 0o077 == 0
    assert stored.parent.stat().st_mode & 0o077 == 0
    response = await client.post("/api/documents", files={"file": (f"{'a' * 300}.md", b"long filename")})
    assert response.status_code == 202
    assert len(response.json()["name"]) == 255


async def test_url_registration_is_honest(client: httpx.AsyncClient) -> None:
    for value in ("file:///etc/passwd", "https://user:secret@example.com", "https://[", "https://x/" + "x" * 2048):
        assert (await client.post("/api/documents", data={"kind": "github", "value": value})).status_code == 422
    response = await client.post("/api/documents", data={"kind": "github", "value": "https://example.com"})
    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert "not configured" in response.json()["error_message"]


async def test_cors(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/api/sources",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers


async def test_lifespan_recovers_pending_job(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/documents",
        files={
            "file": ("queued.md", b"# Queued for restart\n\nRecover this pending upload after restarting the worker.")
        },
    )
    source_id = response.json()["source_id"]
    await close_database()
    async with app.router.lifespan_context(app):
        for _ in range(60):
            detail = (await client.get(f"/api/sources/{source_id}")).json()
            if detail["status"] == "ready_for_embedding":
                break
            await asyncio.sleep(0.1)
        assert detail["status"] == "ready_for_embedding"


async def test_health_unavailable_has_no_secrets(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> None:
        raise OSError("private-database-password")

    monkeypatch.setattr("backend.services.readiness.get_engine", unavailable)
    response = await client.get("/api/readiness")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"
    assert "private" not in response.text


async def test_attachment_only_diagnosis_persists_context(client: httpx.AsyncClient) -> None:
    payload = {"files": [{"name": "worker.py", "content": "await worker.run()"}], "techs": ["Python"]}
    response = await client.post("/api/debug", json=payload)
    assert response.status_code == 200
    diagnosis = response.json()
    assert diagnosis["request"]["files"] == payload["files"]
    assert diagnosis["rag"]["query"] == "await worker.run()"
    assert diagnosis["codeFix"] is None
    assert diagnosis["confidence"] is None
    await close_database()
    assert (await client.get(f"/api/sessions/{diagnosis['sessionId']}")).json() == diagnosis
    assert (await client.get(f"/api/sessions/{diagnosis['sessionId']}/messages")).json() == []
    assert (await client.get(f"/api/sessions/{uuid4()}/messages")).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "bad\x00text"},
        {"files": [{"name": "empty.txt", "content": "   "}]},
        {"files": [{"name": "binary.txt", "content": "bad\x00text"}]},
        {"files": [{"name": "large.txt", "content": "x" * 50_001}]},
        {"files": [{"name": "one.txt", "content": "text"}] * 6},
        {"repo_url": "https://user:password@example.com", "error": "failure"},
    ],
)
async def test_invalid_debug_context(client: httpx.AsyncClient, payload: dict[str, object]) -> None:
    assert (await client.post("/api/debug", json=payload)).status_code == 422


async def test_titles_with_dots_and_readiness_counts(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/documents",
        data={"kind": "docs", "value": "Python 3.14", "content": "A useful asyncio guide for recovery."},
    )
    assert response.status_code == 202
    assert response.json()["name"] == "Python-3.14.md"
    await ingest_source(UUID(response.json()["source_id"]))
    health = (await client.get("/api/readiness")).json()
    assert health["sources"] == health["documents"] == health["chunks"] == 1
    assert health["embedded_chunks"] == health["pending_sources"] == health["failed_sources"] == 0


async def test_health_detects_migration_mismatch(client: httpx.AsyncClient) -> None:
    async with get_session_factory()() as db:
        await db.execute(text("UPDATE alembic_version SET version_num='old'"))
        await db.commit()
    try:
        response = await client.get("/api/readiness")
        assert response.status_code == 503
        assert response.json()["database"] == "connected"
        assert response.json()["schema"] == "migration_required"
    finally:
        async with get_session_factory()() as db:
            await db.execute(text("UPDATE alembic_version SET version_num='0004'"))
            await db.commit()


async def test_provider_receives_evidence_input_and_conversation(client: httpx.AsyncClient) -> None:
    class TestProvider(DocumentationProvider):
        generation = "model"

        async def diagnose(self, payload: DebugRequest, evidence: list[SourceDoc]) -> DiagnosisDraft:
            assert payload.context == "timeout recovery"
            assert evidence and "Retry the timeout" in evidence[0].excerpt
            draft = await super().diagnose(payload, evidence)
            draft.rootCause = "Test provider response"
            return draft

        async def reply(
            self, question: str, diagnosis: Diagnosis, history: list[ChatMessage], evidence: list[SourceDoc]
        ) -> str:
            assert diagnosis.rootCause == "Test provider response"
            assert diagnosis.request and diagnosis.request.context == "timeout recovery"
            assert evidence
            return f"History messages: {len(history)}"

    upload = await client.post("/api/documents", data={"content": "Retry the timeout after checking network recovery."})
    await ingest_source(UUID(upload.json()["source_id"]))
    app.dependency_overrides[get_diagnosis_provider] = TestProvider
    try:
        result = (await client.post("/api/debug", json={"context": "timeout recovery"})).json()
        assert result["generation"] == "model"
        for count in (0, 2):
            response = await client.post("/api/chat", json={"session_id": result["sessionId"], "question": "timeout?"})
            assert response.json()["text"] == f"History messages: {count}"
    finally:
        app.dependency_overrides.pop(get_diagnosis_provider)


async def test_short_query_and_no_evidence_reply(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/debug", json={"error": "?!"})
    assert response.status_code == 200
    diagnosis = response.json()
    assert diagnosis["sources"] == []
    assert diagnosis["status"] == "no-cause"
    response = await client.post("/api/chat", json={"session_id": diagnosis["sessionId"], "question": "Why?"})
    assert response.status_code == 200
    assert response.json()["text"].startswith("No matching documentation found.")
    assert response.json()["sources"] == []
