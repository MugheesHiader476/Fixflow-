"""Durable job, activation and measured retrieval integration. No live model calls."""

import asyncio
import json
import math
import os
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, update

from backend.config import Settings, get_settings
from backend.db.models import (
    ConnectorAccount,
    EmbeddingJob,
    EvidenceEmbedding,
    EvidencePassage,
    KnowledgeSource,
    PassageEmbedding,
    PassageIndex,
)
from backend.db.session import close_database, get_session_factory
from backend.processing.embedding_input import DIMENSIONS, MODEL
from backend.repositories.sources import delete_source
from backend.schemas.evidence import GeminiConfig
from backend.services.embeddings import embedding_worker, process_batch
from backend.services.gemini_embeddings import normalized
from backend.tests.test_embeddings import VECTOR, mock_google, release_rates, response, run_prepare
from backend.tests.test_evidence import indexed, search

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "vector",
    [
        None,
        [],
        [1.0],
        [float("nan")] * DIMENSIONS,
        [float("inf")] * DIMENSIONS,
        [False] * DIMENSIONS,
        [0.0] * DIMENSIONS,
    ],
)
def test_vector_contract(vector: object) -> None:
    with pytest.raises(ValueError):
        normalized(vector)


def test_explicit_configuration_rejects_unsupported_space() -> None:
    assert not GeminiConfig().configured
    assert not GeminiConfig(api_key=SecretStr("not-a-real-key")).configured
    for changes in [{"model": "gemini-embedding-001"}, {"dimensions": 1536}, {"dimensions": 769}]:
        with pytest.raises(ValidationError):
            GeminiConfig.model_validate(changes)


async def test_queue_atomic_activation_and_restart_resume(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "\n\n".join(f"# Section {i}\n\nPayment evidence {i}." for i in range(20)))
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response(len(json.loads(request.content)["requests"]))

    mock_google(monkeypatch, handler)
    url = f"/api/evidence/sources/{identifier}/embed"
    queued = await client.post(url)
    assert queued.status_code == 202 and queued.json()["status"] == "queued" and not calls
    await process_batch(identifier)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 16
        assert await db.scalar(select(func.count()).select_from(PassageEmbedding)) == 0
    partial = await search(client, retrieval="hybrid")
    assert partial.json()["retrieval"] == "lexical" and partial.json()["embedding_calls"] == 0
    assert partial.json()["passages"] and len(calls) == 1
    # A dead worker's lease is durably recoverable by a new worker/session.
    async with get_session_factory().begin() as db:
        await db.execute(update(EmbeddingJob).values(status="running"))
    await release_rates()
    await close_database()
    worker = asyncio.create_task(embedding_worker())
    try:
        async with asyncio.timeout(8):
            while True:
                status = await client.get(f"/api/evidence/sources/{identifier}/embedding-status")
                if status.json()["status"] == "complete":
                    break
                await asyncio.sleep(0.05)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    assert status.json()["active"] and status.json()["prepared"] == 20
    assert status.json()["embedding_calls"] == 2 and len(calls) == 2
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(PassageEmbedding)) == 20
        row = await db.scalar(select(EvidenceEmbedding))
        index = await db.get(PassageIndex, identifier)
        assert row and index and row.model == row.revision == MODEL and row.dimensions == DIMENSIONS
        assert row.input_format_version == "search-v1" and index.vector_fingerprint == index.fingerprint


async def test_workers_share_claim_and_duplicate_queue_is_idempotent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = await indexed(client, "Payment evidence under concurrent preparation.")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        entered.set()
        await release.wait()
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await client.post(f"/api/evidence/sources/{identifier}/embed")).status_code == 202
    first = asyncio.create_task(process_batch(identifier))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        await process_batch(identifier)
        assert (await client.post(f"/api/evidence/sources/{identifier}/embed")).json()["status"] == "running"
        assert len(calls) == 1
    finally:
        release.set()
        await first
    assert (await run_prepare(client, identifier)).json()["active"]
    assert len(calls) == 1


async def test_revision_mismatch_and_rate_failure_keep_lexical(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = await indexed(client, "Payment credentials protect release.")

    async def handler(request: httpx.Request) -> httpx.Response:
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).json()["active"]
    monkeypatch.setenv("GEMINI__REVISION", "operator-rollout-2")
    get_settings.cache_clear()
    result = await search(client, retrieval="hybrid")
    assert result.json()["retrieval"] == "lexical" and result.json()["embedding_calls"] == 0
    assert result.json()["passages"]
    await release_rates()
    assert (await run_prepare(client, identifier)).json()["active"]
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 2
    monkeypatch.setenv("GEMINI__REQUESTS_PER_MINUTE", "1")
    get_settings.cache_clear()
    limited = await search(client, retrieval="hybrid")
    assert limited.status_code == 200 and limited.json()["retrieval"] == "lexical"
    assert limited.json()["embedding_calls"] == 0 and limited.json()["passages"]
    assert any("rate limited" in warning for warning in limited.json()["warnings"])


async def test_retries_are_bounded_and_malformed_batches_never_publish(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = await indexed(client, "Payment evidence for retry isolation.")
    fail = "network"

    async def handler(request: httpx.Request) -> httpx.Response:
        if fail == "network":
            raise httpx.ReadTimeout("secret upstream message")
        return response(2)  # There is only one pending input.

    mock_google(monkeypatch, handler)
    assert (await client.post(f"/api/evidence/sources/{identifier}/embed")).status_code == 202
    for _ in range(3):
        await release_rates()
        await process_batch(identifier)
    status = await client.get(f"/api/evidence/sources/{identifier}/embedding-status")
    assert status.json()["status"] == "failed" and status.json()["embedding_calls"] == 3
    assert "secret" not in status.text
    fail = "malformed"
    await release_rates()
    invalid = await run_prepare(client, identifier)
    assert invalid.json()["status"] == "failed" and not invalid.json()["active"]
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(EvidenceEmbedding)) == 0
        assert await db.scalar(select(func.count()).select_from(PassageEmbedding)) == 0


async def test_selection_budget_and_rank_metadata(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier = await indexed(client, "Payment authorization requires a valid credential.")

    async def handler(request: httpx.Request) -> httpx.Response:
        return response(1)

    mock_google(monkeypatch, handler)
    await run_prepare(client, identifier)
    result = (
        await search(
            client,
            question="Payment authorization",
            retrieval="hybrid",
            max_passages=1,
            context_tokens=100,
            candidate_limit=2,
        )
    ).json()
    assert len(result["passages"]) == 1 and result["evidence_tokens"] <= 100
    hit = result["passages"][0]
    assert hit["lexical_rank"] == hit["vector_rank"] == 1 and hit["similarity"] == 1
    assert hit["embedding_profile"] and hit["passage"]["spans"]
    assert (await search(client, context_tokens=1)).json()["passages"] == []
    assert (await search(client, candidate_limit=101)).status_code == 422


async def test_lexical_hybrid_benchmark(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Controlled vector fixture measures ranking plumbing, not live model quality."""
    corpus = [
        (
            "Payment webhook retries use idempotency keys to prevent duplicate charges.",
            "How do we prevent duplicate charges?",
        ),
        ("Authorization expires when credentials are revoked.", "Explain login invalidation"),
        ("Slack history reads reserve a durable slot once per minute.", "Why do channel imports wait?"),
        (
            "Migration downgrade refuses while parsed records exist to preserve data.",
            "How does rollback protect stored documents?",
        ),
        (
            "GitHub shared repositories require the intersection of installation and user permissions.",
            "Who may read connected code?",
        ),
        (
            "PDF gutter spanning layouts require review because reading order is uncertain.",
            "Why did the two column scan fail?",
        ),
    ]
    ids = [await indexed(client, body) for body, _ in corpus]
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        vectors = []
        for item in json.loads(request.content)["requests"]:
            text = item["content"]["parts"][0]["text"]
            calls.append("query" if text.startswith("task:") else "document")
            matches = [i for i, (body, question) in enumerate(corpus) if body in text or question in text]
            assert len(matches) == 1
            vector = [0.0] * DIMENSIONS
            vector[matches[0]] = 1.0
            vectors.append({"values": vector})
        return httpx.Response(200, json={"embeddings": vectors, "usageMetadata": {"promptTokenCount": 123}})

    mock_google(monkeypatch, handler)
    monkeypatch.setenv("GEMINI__REQUESTS_PER_MINUTE", "30")
    get_settings.cache_clear()
    for identifier in ids:
        await release_rates()
        assert (await run_prepare(client, identifier)).json()["active"]
    await release_rates()
    report: dict[str, object] = {
        "boundary": "local ASGI + PostgreSQL; deterministic mocked embeddings",
        "sources": len(ids),
        "ingestion_embedding_calls": len(calls),
        "ingestion_reported_tokens": 123 * len(ids),
    }
    for mode in ["lexical", "hybrid"]:
        times, reciprocal, ndcg, recalls = [], [], [], []
        embedding_calls = embedding_tokens = 0
        for _ in range(3):
            for identifier, (_, question) in zip(ids, corpus, strict=True):
                started = time.perf_counter()
                result = await search(client, question=question, retrieval=mode, max_passages=3, expand_context=False)
                times.append((time.perf_counter() - started) * 1000)
                assert result.status_code == 200, result.text
                data = result.json()
                ranked = sorted(data["passages"], key=lambda hit: -hit["score"])
                position = next(
                    (i + 1 for i, hit in enumerate(ranked) if hit["passage"]["source_id"] == str(identifier)), None
                )
                recalls.append(int(position is not None))
                reciprocal.append(1 / position if position else 0)
                ndcg.append(1 / math.log2(position + 1) if position else 0)
                embedding_calls += data["embedding_calls"]
                embedding_tokens += data["embedding_input_tokens"] or 0
        report[mode] = {
            "requests": len(times),
            "recall_at_3": statistics.mean(recalls),
            "mrr_at_3": statistics.mean(reciprocal),
            "ndcg_at_3": statistics.mean(ndcg),
            "p50_ms": round(statistics.median(times), 2),
            "p95_ms": round(sorted(times)[math.ceil(len(times) * 0.95) - 1], 2),
            "embedding_calls": embedding_calls,
            "embedding_input_tokens": embedding_tokens,
        }
        if mode == "hybrid":
            assert all(recalls) and embedding_calls == 18
        else:
            assert embedding_calls == 0
    print("VECTOR_BENCHMARK " + json.dumps(report, sort_keys=True))


@pytest.mark.parametrize(
    "state", ["inactive", "expired", "processing", "disconnected", "revoked", "credential_loss", "deleted"]
)
async def test_activated_vectors_follow_current_permissions(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    identifier = await indexed(client, "Payment access is private to the connecting owner.")
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).json()["active"]
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source
        if state == "deleted":
            db.info["owner_id"] = "user_test"
            assert await delete_source(db, identifier)
        elif state == "inactive":
            source.is_active = False
        elif state == "expired":
            source.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state == "processing":
            source.status = state
        else:
            account = ConnectorAccount(
                owner_id="user_test",
                provider="slack",
                external_account_id="team",
                display_name="Team",
                status="connected",
                authentication_status="valid",
            )
            db.add(account)
            await db.flush()
            source.connector_account_id = account.id
            if state == "credential_loss":
                account.authentication_status = "reauth_required"
            else:
                account.status = state
        if state != "deleted":
            passage = await db.scalar(select(EvidencePassage).where(EvidencePassage.source_id == identifier))
            assert passage
            passage.payload = {"corrupt": "Must be filtered before payload decoding"}
    result = await search(client, retrieval="hybrid")
    assert result.status_code == 200 and not result.json()["passages"], result.text
    assert result.json()["embedding_calls"] == 0 and len(calls) == 1
    if state == "deleted":
        async with get_session_factory()() as db:
            for table in [EvidenceEmbedding, PassageEmbedding, EmbeddingJob]:
                assert await db.scalar(select(func.count()).select_from(table)) == 0


async def test_incompatible_cache_fails_without_repeated_provider_calls(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = await indexed(client, "Payment evidence for a cache integrity check.")
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response(1)

    mock_google(monkeypatch, handler)
    assert (await run_prepare(client, identifier)).json()["active"]
    async with get_session_factory().begin() as db:
        await db.execute(update(EvidenceEmbedding).values(model="incompatible-model"))
    result = await run_prepare(client, identifier)
    assert result.json()["status"] == "failed" and not result.json()["active"] and len(calls) == 1
    await release_rates()
    await process_batch(identifier)
    assert len(calls) == 1
    lexical = await search(client, retrieval="hybrid")
    assert lexical.json()["retrieval"] == "lexical" and lexical.json()["passages"]


async def test_migration_preserves_0008_vectors_without_activation(client: httpx.AsyncClient) -> None:
    identifier = await indexed(client, "Payment evidence for additive migration preservation.")
    old_profile = MODEL + ":768:search-v1"
    async with get_session_factory().begin() as db:
        passage = await db.scalar(select(EvidencePassage).where(EvidencePassage.source_id == identifier))
        assert passage
        db.add(
            EvidenceEmbedding(
                source_id=identifier, profile=old_profile, input_hash=passage.embedding_input_hash, embedding=VECTOR
            )
        )
    await close_database()
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    try:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "downgrade", "0008"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    finally:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    await close_database()
    async with get_session_factory()() as db:
        row = await db.scalar(select(EvidenceEmbedding))
        index = await db.get(PassageIndex, identifier)
        assert row and row.profile == old_profile and row.revision == "legacy-unverified"
        assert list(row.embedding) == VECTOR
        assert index and index.vector_fingerprint is None and index.vector_profile is None
    lexical = await search(client, retrieval="hybrid")
    assert lexical.json()["passages"] and lexical.json()["embedding_calls"] == 0


def test_embedding_dimensions_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI__MODEL", MODEL)
    monkeypatch.setenv("GEMINI__DIMENSIONS", "768")
    assert Settings().gemini.dimensions == 768
    monkeypatch.setenv("GEMINI__DIMENSIONS", "1536")
    with pytest.raises(ValidationError):
        Settings()
