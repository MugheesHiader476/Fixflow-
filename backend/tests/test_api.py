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
    assert health["revision"] == health["expected_revision"] == "0010"
    assert health["sources"] == health["documents"] == health["chunks"] == health["embedded_chunks"] == 0
    assert health["ai_generation"] == "removed"


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
    assert detail["status"] == "parsed"
    assert detail["document_count"] == 1
    assert detail["chunk_count"] == 0
    assert detail["retrieval_available"] is False
    assert (await client.get(f"/api/sources/{source_id}/status")).json() == detail
    assert (await client.get("/api/sources")).json() == [detail]
    async with get_session_factory()() as session:
        assert not list(await session.scalars(select(DocumentChunk)))


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


async def test_existing_sessions_messages_and_saved_snapshots_remain_private(client: httpx.AsyncClient) -> None:
    from backend.tests.history import seed_history  # noqa: PLC0415

    diagnosis, reply = await seed_history()
    saved = await client.post("/api/saved", json={"problem": "Failure", "rootCause": "Cause", "fixSummary": "Fix"})
    assert saved.status_code == 200
    await close_database()
    sid = diagnosis["sessionId"]
    assert (await client.get(f"/api/sessions/{sid}")).json() == diagnosis
    assert (await client.get(f"/api/sessions/{sid}/messages")).json() == [reply]
    assert len((await client.get("/api/sessions")).json()) == 1
    assert (await client.get("/api/saved")).json() == [saved.json()]
    other = {"X-FixFlow-User-Id": "user_other"}
    assert (await client.get(f"/api/sessions/{sid}", headers=other)).status_code == 404
    assert (await client.get(f"/api/sessions/{sid}/messages", headers=other)).status_code == 404
    assert (await client.get("/api/saved", headers=other)).json() == []


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/ask", {"question": "What is this?"}),
        ("/api/debug", {"error": "Example error"}),
        ("/api/chat", {"session_id": "old-session", "question": "Why?"}),
        ("/api/sources/00000000-0000-0000-0000-000000000001/retry-embedding", {}),
    ],
)
async def test_removed_stages_return_gone_without_creating_data(
    client: httpx.AsyncClient, path: str, payload: dict[str, object]
) -> None:
    response = await client.post(path, json=payload)
    assert response.status_code == 410
    assert "removed" in response.json()["error"]["message"]
    assert (await client.get("/api/sessions")).json() == []


async def test_invalid_requests_use_safe_errors(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/debug", json={})).status_code == 422
    assert (await client.post("/api/debug", json={"context": "   "})).status_code == 422
    assert (await client.post("/api/chat", json={"session_id": "missing", "question": "hello"})).status_code == 410
    assert (await client.post("/api/chat", json={"session_id": "x", "question": " "})).status_code == 422
    assert (await client.get(f"/api/sources/{uuid4()}")).status_code == 404
    assert (await client.get("/api/sources/not-uuid")).status_code == 422


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
            if detail["status"] == "parsed":
                break
            await asyncio.sleep(0.1)
        assert detail["status"] == "parsed"


async def test_health_unavailable_has_no_secrets(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> None:
        raise OSError("private-database-password")

    monkeypatch.setattr("backend.services.readiness.get_engine", unavailable)
    response = await client.get("/api/readiness")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"
    assert "private" not in response.text


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
    assert health["sources"] == health["documents"] == 1
    assert health["chunks"] == 0
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
            await db.execute(text("UPDATE alembic_version SET version_num='0010'"))
            await db.commit()
