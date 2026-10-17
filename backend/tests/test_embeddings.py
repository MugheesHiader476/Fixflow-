"""Gemini HTTP is mocked; authorization, pgvector and migrations use real PostgreSQL."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update

from backend.config import get_settings
from backend.db.models import (
    ConnectorRateLimit,
    EmbeddingJob,
    EvidenceEmbedding,
    EvidencePassage,
    KnowledgeSource,
    PassageEmbedding,
    PassageIndex,
)
from backend.db.session import get_session_factory
from backend.processing.embedding_input import DIMENSIONS, PROFILE
from backend.schemas.evidence import GeminiConfig
from backend.services.embeddings import activate, process_batch
from backend.services.gemini_embeddings import EmbeddingUsage, embed
from backend.services.ingestion import ingest_source
from backend.tests.test_evidence import indexed, search

pytestmark = pytest.mark.anyio
VECTOR = [1.0] + [0.0] * (DIMENSIONS - 1)


def mock_google(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
) -> None:
    monkeypatch.setenv("GEMINI__API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("GEMINI__MODEL", "gemini-embedding-2")
    get_settings.cache_clear()
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "backend.services.gemini_embeddings.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def response(count: int, vector: list[float] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "embeddings": [{"values": vector or VECTOR} for _ in range(count)],
            "usageMetadata": {"promptTokenCount": 123},
        },
    )


async def release_rates() -> None:
    async with get_session_factory().begin() as db:
        await db.execute(update(EmbeddingJob).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.execute(update(ConnectorRateLimit).values(next_allowed_at=datetime.now(UTC) - timedelta(seconds=1)))


async def run_prepare(client: httpx.AsyncClient, identifier: object) -> httpx.Response:
    queued = await client.post(f"/api/evidence/sources/{identifier}/embed")
    if queued.status_code != 202:
        return queued

    await process_batch(UUID(str(identifier)))
    return await client.get(f"/api/evidence/sources/{identifier}/embedding-status")


async def test_separate_contents_no_truncation_and_normalized_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert (
            request.url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents"
        )
        assert "key=" not in str(request.url)
        body = json.loads(request.content)
        assert len(body["requests"]) == 2
        for item, expected in zip(body["requests"], ["first passage", "second passage"], strict=True):
            assert item["model"] == "models/gemini-embedding-2"
            assert item["content"]["parts"] == [{"text": expected}]
            assert item["embedContentConfig"] == {"outputDimensionality": 768, "autoTruncate": False}
            assert "taskType" not in item
        return response(2, [3.0, 4.0] + [0.0] * 766)

    mock_google(monkeypatch, handler)
    usage = EmbeddingUsage()
    vectors = await embed(
        ["first passage", "second passage"], GeminiConfig(api_key=SecretStr("test"), model="gemini-embedding-2"), usage
    )
    assert vectors[0][:2] == [0.6, 0.8] and len(calls) == 1
    assert usage.calls == 1 and usage.input_tokens == 123


@pytest.mark.parametrize("bad", ["aggregate", "dimension", "zero", "boolean", "oversize", "redirect", "timeout"])
async def test_invalid_provider_outputs_fail_once(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if bad == "timeout":
            raise httpx.ReadTimeout("private provider error must not reach clients")
        if bad == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example/steal"})
        if bad == "oversize":
            return httpx.Response(200, content=b"x" * (512 * 1024 + 1))
        if bad == "aggregate":
            return response(1)
        if bad == "dimension":
            return response(2, [1.0])
        if bad == "boolean":
            return response(2, [True, *VECTOR[1:]])
        return response(2, [0.0] * DIMENSIONS)

    mock_google(monkeypatch, handler)
    with pytest.raises((ValueError, httpx.HTTPError)):
        await embed(
            ["first", "second"], GeminiConfig(api_key=SecretStr("test"), model="gemini-embedding-2"), EmbeddingUsage()
        )
    assert len(calls) == 1


async def test_prepare_resume_reuse_and_durable_rate_limit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "\n\n".join(f"# Heading {i}\n\nEvidence item {i}." for i in range(20)))
    calls = []
    fail = False

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if fail:
            raise httpx.ReadTimeout("private upstream failure")
        assert all(
            item["content"]["parts"][0]["text"].startswith("title: none | text: Project: Atlas")
            for item in body["requests"]
        )
        return response(len(body["requests"]))

    mock_google(monkeypatch, handler)
    first = await run_prepare(client, identifier)
    assert first.status_code == 200, first.text
    assert (first.json()["prepared"], first.json()["remaining"], first.json()["input_tokens"]) == (16, 4, 123)
    assert (await run_prepare(client, identifier)).json()["embedding_calls"] == 1
    await release_rates()
    fail = True
    failed = await run_prepare(client, identifier)
    assert failed.status_code == 200 and "private upstream" not in failed.text
    assert failed.json()["status"] == "queued" and not failed.json()["active"]
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 16
    await release_rates()
    fail = False
    second = await run_prepare(client, identifier)
    assert second.status_code == 200 and second.json()["remaining"] == 0, second.text
    assert [len(call["requests"]) for call in calls] == [16, 4, 4]
    unchanged = await run_prepare(client, identifier)
    assert unchanged.json()["embedding_calls"] == 3 and len(calls) == 3
    assert unchanged.json()["status"] == "complete" and unchanged.json()["active"]
    # A fresh session/request uses durable rows, not a process-local cache.
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 20
        assert set(await db.scalars(select(EvidenceEmbedding.profile))) == {PROFILE}


async def test_hybrid_finds_paraphrase_and_keeps_lexical_default(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "Authorization expires when credentials are revoked.")
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return response(len(body["requests"]))

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).status_code == 200
    lexical = await search(client, question="Explain login invalidation")
    assert lexical.json()["passages"] == [] and lexical.json()["embedding_calls"] == 0
    hybrid = await search(client, question="Explain login invalidation", retrieval="hybrid")
    assert hybrid.status_code == 200, hybrid.text
    data = hybrid.json()
    assert data["retrieval"] == "hybrid" and data["embedding_calls"] == 1 and data["embedding_input_tokens"] == 123
    assert data["passages"][0]["passage"]["source_id"] == str(identifier)
    assert (
        calls[-1]["requests"][0]["content"]["parts"][0]["text"]
        == "task: search result | query: Explain login invalidation"
    )
    assert len(calls) == 2
    exact = await search(client, question="What is the latest email?", retrieval="hybrid")
    assert exact.json()["passages"] == [] and len(calls) == 2


@pytest.mark.parametrize("action", ["revoke", "delete", "update", "reassign"])
async def test_changes_during_embedding_discard_vectors(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    identifier = await indexed(client, "Private payment evidence.")

    async def handler(request: httpx.Request) -> httpx.Response:
        if action in {"revoke", "delete"}:
            async with get_session_factory().begin() as db:
                if action == "delete":
                    await db.execute(delete(KnowledgeSource).where(KnowledgeSource.id == identifier))
                else:
                    await db.execute(
                        update(KnowledgeSource).where(KnowledgeSource.id == identifier).values(is_active=False)
                    )
        elif action == "update":
            changed = await client.post(
                "/api/documents", data={"content": "Changed payment evidence.", "update_source_id": str(identifier)}
            )
            assert changed.status_code == 202
            await ingest_source(identifier)
        else:
            changed = await client.post(f"/api/evidence/sources/{identifier}/index", json={"project": "Other"})
            assert changed.status_code == 200
        return response(1)

    mock_google(monkeypatch, handler)
    result = await run_prepare(client, identifier)
    assert result.status_code in {200, 404}, result.text
    async with get_session_factory()() as db:
        job = await db.get(EmbeddingJob, identifier)
        assert job is None or job.status == "cancelled"
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 0


async def test_owner_isolation_unchanged_input_reuse_and_pruning(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = "# Stable\n\nPayment testing blocks release.\n\n# Changing\n\nOld evidence."
    identifier = await indexed(client, original)
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return response(len(body["requests"]))

    mock_google(monkeypatch, handler)
    url = f"/api/evidence/sources/{identifier}/embed"
    assert (await run_prepare(client, identifier)).json()["prepared"] == 2
    foreign = await client.post(url, headers={"X-FixFlow-User-Id": "user_other"})
    assert foreign.status_code == 404 and len(calls) == 1
    changed = await client.post(
        "/api/documents",
        data={
            "content": original.replace("Old evidence.", "Replacement evidence."),
            "update_source_id": str(identifier),
        },
    )
    assert changed.status_code == 202
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 1
        assert await db.scalar(select(func.count()).select_from(PassageEmbedding)) == 0
    fallback = await search(client, retrieval="hybrid")
    assert fallback.json()["retrieval"] == "lexical" and fallback.json()["embedding_calls"] == 0
    await release_rates()
    result = await run_prepare(client, identifier)
    assert result.status_code == 200 and result.json()["prepared"] == 2, result.text
    assert [len(call["requests"]) for call in calls] == [2, 1]
    removed = await client.post(f"/api/evidence/sources/{identifier}/remove-index")
    assert removed.status_code == 200
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 0
        assert await db.get(KnowledgeSource, identifier)


async def test_hybrid_partial_failure_and_revocation_do_not_leak(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "Payment testing blocks release.")
    await indexed(client, "Unprepared payment evidence.")
    phase = "prepare"

    async def handler(request: httpx.Request) -> httpx.Response:
        if phase == "failure":
            raise httpx.ReadTimeout("private upstream details")
        if phase == "revoked":
            async with get_session_factory().begin() as db:
                await db.execute(update(KnowledgeSource).values(is_active=False))
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).status_code == 200
    phase = "failure"
    failure = await search(client, retrieval="hybrid")
    assert failure.status_code == 200, failure.text
    data = failure.json()
    assert data["passages"] and data["embedding_calls"] == 1 and data["retrieval"] == "lexical"
    assert any("partial" in warning for warning in data["warnings"])
    assert any("failed" in warning for warning in data["warnings"])
    assert "private upstream" not in failure.text
    phase = "revoked"
    revoked = await search(client, retrieval="hybrid")
    assert revoked.status_code == 200 and revoked.json()["passages"] == []


async def test_0007_rows_prepare_without_reindex_and_cache_hash_tamper_rejected(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "Payment testing migration evidence.")
    async with get_session_factory().begin() as db:
        await db.execute(update(EvidencePassage).values(embedding_input_hash=None))

    async def handler(request: httpx.Request) -> httpx.Response:
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).json()["prepared"] == 1
    async with get_session_factory().begin() as db:
        await db.execute(update(EvidencePassage).values(embedding_input_hash="a" * 64))
    assert (await search(client)).status_code == 409


async def test_dense_access_filter_precedes_decode_and_similarity_cutoff(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = await indexed(client, "Authorization requires a valid credential.")
    project = await indexed(client, "Private payment project.", project="Other")
    foreign = await indexed(client, "Private payment owner.", **{"X-FixFlow-User-Id": "user_other"})
    expired = await indexed(client, "Private payment expired.")
    async with get_session_factory().begin() as db:
        rows = list(await db.scalars(select(EvidencePassage)))
        for row in rows:
            db.add(
                EvidenceEmbedding(
                    source_id=row.source_id,
                    profile=PROFILE,
                    input_hash=row.embedding_input_hash,
                    embedding=VECTOR,
                    revision="gemini-embedding-2",
                )
            )
            if row.source_id != allowed:
                row.payload = {"corrupt": "Must be filtered in SQL before decoding"}
        await db.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.id == expired)
            .values(access_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    async with get_session_factory().begin() as db:
        for identifier in [allowed, project, foreign, expired]:
            index = await db.get(PassageIndex, identifier)
            assert index is not None
            job = EmbeddingJob(source_id=identifier, source_version=index.fingerprint, profile=PROFILE)
            db.add(job)
            await db.flush()
            await activate(db, job, index, get_settings().gemini)
    vector = VECTOR
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response(1, vector)

    mock_google(monkeypatch, handler)
    result = await search(client, question="Explain login invalidation", retrieval="hybrid")
    assert result.status_code == 200, result.text
    assert [hit["passage"]["source_id"] for hit in result.json()["passages"]] == [str(allowed)]
    assert not any("partial" in warning for warning in result.json()["warnings"])
    for identifier in [project, foreign, expired]:
        excluded = await search(
            client, question="Explain login invalidation", retrieval="hybrid", source_ids=[str(identifier)]
        )
        assert excluded.json()["passages"] == [] and excluded.json()["embedding_calls"] == 0
    assert len(calls) == 1
    vector = [0.0, 1.0] + [0.0] * (DIMENSIONS - 2)
    unrelated = await search(client, question="Explain login invalidation", retrieval="hybrid")
    assert unrelated.status_code == 200 and unrelated.json()["passages"] == []


async def test_preparation_requires_configuration_and_hybrid_falls_back_without_paid_calls(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI__API_KEY", "")
    get_settings.cache_clear()
    identifier = await indexed(client, "Payment testing blocks release.")
    assert (await run_prepare(client, identifier)).status_code == 503
    result = await search(client, retrieval="hybrid")
    assert result.status_code == 200 and result.json()["passages"]
    assert result.json()["embedding_calls"] == 0 and result.json()["retrieval"] == "lexical"


async def test_migration_refuses_to_discard_populated_vectors(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "Payment testing vector preservation evidence.")

    async def handler(request: httpx.Request) -> httpx.Response:
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).status_code == 200
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "downgrade", "0007"],
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0 and "Cannot downgrade while vector memberships or jobs exist" in result.stderr
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 1
        assert await db.get(KnowledgeSource, identifier)
