"""Real PostgreSQL authorization/update tests; external synthesis HTTP is mocked."""

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, func, select

from backend.config import get_settings
from backend.connectors.core import EnvelopeFactory
from backend.db.models import ConnectorAccount, EvidencePassage, IngestionArtifact, KnowledgeSource, PassageIndex
from backend.db.session import get_session_factory
from backend.schemas.connectors import Resource
from backend.services.connector_sources import register
from backend.services.ingestion import ingest_source

pytestmark = pytest.mark.anyio


async def indexed(client: httpx.AsyncClient, body: str, project: str = "Atlas", **headers: str) -> UUID:
    response = await client.post("/api/documents", data={"content": body, "value": "Weekly Report"}, headers=headers)
    assert response.status_code == 202, response.text
    identifier = UUID(response.json()["id"])
    await ingest_source(identifier)
    receipt = await client.post(
        f"/api/evidence/sources/{identifier}/index",
        json={
            "project": project,
            "title": "Weekly Report",
            "document_date": "2026-10-16",
        },
        headers=headers,
    )
    assert receipt.status_code == 200, receipt.text
    return identifier


async def search(client: httpx.AsyncClient, **options: object) -> httpx.Response:
    return await client.post(
        "/api/evidence/search",
        json={"question": "Why is payment testing blocking release?", "project": "Atlas", **options},
    )


async def test_project_owner_filters_run_before_private_decode_and_no_implicit_backfill(
    client: httpx.AsyncClient,
) -> None:
    await indexed(client, "# Release blockers\n\nPayment testing is incomplete and blocks release.")
    other_project = await indexed(client, "Payment testing in another project.", project="Other")
    other_owner = await indexed(client, "Other owner's private payment testing.", **{"X-FixFlow-User-Id": "user_other"})
    unindexed = await client.post("/api/documents", data={"content": "Payment testing unindexed source."})
    await ingest_source(UUID(unindexed.json()["id"]))
    async with get_session_factory().begin() as db:
        for identifier in [other_project, other_owner]:
            passage = await db.scalar(select(EvidencePassage).where(EvidencePassage.source_id == identifier))
            assert passage
            passage.payload = {"corrupt": "must not be decoded"}
    result = await search(client)
    assert result.status_code == 200, result.text
    hits = result.json()["passages"]
    assert len(hits) == 1 and "incomplete" in hits[0]["passage"]["content"]
    assert (await search(client, source_ids=[str(other_owner)])).json()["passages"] == []
    assert (
        await client.post(f"/api/evidence/sources/{other_owner}/index", json={"project": "Atlas"})
    ).status_code == 404


@pytest.mark.parametrize(
    "state", ["inactive", "expired", "processing", "failed", "legacy", "disconnected", "revoked", "credential_loss"]
)
async def test_unavailable_passages_are_filtered_before_decode(client: httpx.AsyncClient, state: str) -> None:
    identifier = await indexed(client, "Private payment testing evidence.")
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        passage = await db.scalar(select(EvidencePassage).where(EvidencePassage.source_id == identifier))
        assert source and passage
        passage.payload = {"corrupt": True}
        if state == "inactive":
            source.is_active = False
        elif state == "expired":
            source.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state == "legacy":
            source.status = "indexed"
        elif state in {"processing", "failed"}:
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
    result = await search(client)
    assert result.status_code == 200 and result.json()["passages"] == [], result.text


async def test_update_replaces_index_and_failed_update_never_serves_old_passages(client: httpx.AsyncClient) -> None:
    identifier = await indexed(client, "Payment testing is incomplete. Release blocked.")
    old_ids = [h["passage"]["id"] for h in (await search(client)).json()["passages"]]
    update = await client.post(
        "/api/documents",
        data={"content": "Payment testing passed. Release approved.", "update_source_id": str(identifier)},
    )
    assert update.status_code == 202
    assert (await search(client)).json()["passages"] == []
    await ingest_source(identifier)
    hits = (await search(client)).json()["passages"]
    assert hits and "approved" in hits[0]["passage"]["content"]
    assert all(h["passage"]["id"] not in old_ids for h in hits)
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source
        source.status = "failed"
    assert (await search(client)).json()["passages"] == []


async def test_identical_index_reuses_passages_and_removal_preserves_canonical(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = await indexed(client, "Payment testing blocks release.")

    def forbidden(*args, **kwargs):
        raise AssertionError("Unchanged content/specification must reuse index")

    monkeypatch.setattr("backend.repositories.evidence.build_passages", forbidden)
    receipt = await client.post(
        f"/api/evidence/sources/{identifier}/index",
        json={"project": "Atlas", "title": "Weekly Report", "document_date": "2026-10-16"},
    )
    assert receipt.status_code == 200, receipt.text
    removed = await client.post(f"/api/evidence/sources/{identifier}/remove-index")
    assert removed.status_code == 200
    async with get_session_factory().begin() as db:
        assert await db.get(IngestionArtifact, identifier)
        assert await db.get(PassageIndex, identifier) is None
        assert await db.scalar(select(func.count()).select_from(EvidencePassage)) == 0
        await db.execute(delete(KnowledgeSource).where(KnowledgeSource.id == identifier))
    assert (await search(client)).json()["passages"] == []


async def test_source_deletion_cascades_passage_index(client: httpx.AsyncClient) -> None:
    identifier = await indexed(client, "Payment testing cascade evidence.")
    async with get_session_factory().begin() as db:
        await db.execute(delete(KnowledgeSource).where(KnowledgeSource.id == identifier))
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(PassageIndex)) == 0
        assert await db.scalar(select(func.count()).select_from(EvidencePassage)) == 0


async def test_migration_refuses_to_discard_populated_indexes(client: httpx.AsyncClient) -> None:
    identifier = await indexed(client, "Payment testing migration preservation evidence.")
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "downgrade", "0006"],
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "Cannot downgrade while passage indexes exist" in result.stderr
    async with get_session_factory()() as db:
        assert await db.get(IngestionArtifact, identifier)
        assert await db.get(PassageIndex, identifier)
        assert await db.scalar(select(func.count()).select_from(EvidencePassage)) == 1


async def test_deduplicate_without_discarding_conflicting_numeric_facts(client: httpx.AsyncClient) -> None:
    await indexed(client, "Payment testing has 3 remaining failures.")
    await indexed(client, "# Copy\n\nPayment testing has 3 remaining failures.")
    await indexed(client, "Payment testing has 7 remaining failures.")
    hits = (await search(client)).json()["passages"]
    assert len(hits) == 2
    assert {h["passage"]["content"] for h in hits} == {
        "Payment testing has 3 remaining failures.",
        "Payment testing has 7 remaining failures.",
    }


async def test_similarity_does_not_erase_negations_or_changed_status(client: httpx.AsyncClient) -> None:
    common = "Payment testing covers the release integration flow, rollback behavior, billing events and reporting. "
    await indexed(client, common * 3 + "The release is approved.")
    await indexed(client, common * 3 + "The release is not approved.")
    hits = (await search(client)).json()["passages"]
    assert len(hits) == 2
    assert any("not approved" in h["passage"]["content"] for h in hits)


async def test_neighbour_context_stays_in_heading_run(client: httpx.AsyncClient) -> None:
    await indexed(
        client,
        "# Release\n\n"
        + "Payment testing is incomplete. "
        + "Additional scheduling details. " * 120
        + "\n\n# Unrelated\n\nBananas and pears.",
    )
    response = await search(client, question="Payment testing", expand_context=True)
    hits = response.json()["passages"]
    assert len(hits) >= 2 and any(h["reason"] == "neighbour" for h in hits)
    assert all("Bananas" not in h["passage"]["content"] for h in hits)
    assert len(hits) <= 6 and response.json()["evidence_tokens"] <= 3000


async def test_slack_assembles_authorized_root_and_replies_in_original_order(client: httpx.AsyncClient) -> None:
    async with get_session_factory().begin() as db:
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
        factory = EnvelopeFactory(account.id, "user_test", "slack", "team")
        messages = [
            ("100.000000", "Release meeting context."),
            ("100.000001", "Payment testing is incomplete."),
            ("100.000002", "Finance still needs to approve the rollout."),
        ]
        identifiers = []
        for timestamp, content in messages:
            metadata: dict[str, object] = {
                "channel_id": "C1",
                "channel_name": "release",
                "message_ts": timestamp,
                "thread_ts": "100.000000",
            }
            resource = Resource(
                id="C1:" + timestamp,
                name="release " + timestamp,
                kind="slack_message",
                parent_id="C1:100.000000",
                metadata=metadata,
            )
            raw = (
                "# "
                + resource.name
                + "\n\n"
                + "\n".join(f"{key}: {json.dumps(value)}" for key, value in metadata.items())
                + "\n\n"
                + content
            )
            envelope = factory.text(resource, raw, "message.md", "text/markdown")
            await register(db, account, envelope)
            identifiers.append(envelope.source_id)
    for identifier in identifiers:
        await ingest_source(identifier)
        assert (
            await client.post(f"/api/evidence/sources/{identifier}/index", json={"project": "Atlas"})
        ).status_code == 200
    hits = (await search(client, question="Payment testing", expand_context=True)).json()["passages"]
    assert len(hits) == 2
    assert any(h["reason"] == "thread_root" for h in hits)
    assert {h["passage"]["metadata"]["message_id"] for h in hits} == {"100.000000", "100.000001"}
    assert [h["passage"]["metadata"]["message_id"] for h in hits] == ["100.000000", "100.000001"]
    assert sum(h["passage"]["tokens"] for h in hits) <= 600
    async with get_session_factory().begin() as db:
        root = await db.get(KnowledgeSource, identifiers[0])
        assert root
        root.is_active = False
    hits = (await search(client, question="Payment testing", expand_context=True)).json()["passages"]
    assert all(h["passage"]["source_id"] != str(identifiers[0]) for h in hits)


async def test_corrupted_authorized_span_fails_closed(client: httpx.AsyncClient) -> None:
    identifier = await indexed(client, "Payment testing requires strong source grounding.")
    async with get_session_factory().begin() as db:
        row = await db.scalar(select(EvidencePassage).where(EvidencePassage.source_id == identifier))
        assert row
        row.payload = {**row.payload, "content": "Invented evidence"}
    response = await search(client)
    assert response.status_code == 409
    assert "Invented evidence" not in response.text
    rebuilt = await client.post(
        f"/api/evidence/sources/{identifier}/index",
        json={
            "project": "Atlas",
            "title": "Weekly Report",
            "document_date": "2026-10-16",
            "force": True,
        },
    )
    assert rebuilt.status_code == 200
    assert (await search(client)).status_code == 200


async def test_exact_fact_questions_do_not_use_snapshot_retrieval(client: httpx.AsyncClient) -> None:
    for question in ["What is the latest email?", "How many messages?", "What is the current GitHub status?"]:
        response = await client.post(
            "/api/evidence/answer", json={"question": question, "project": "Atlas", "synthesize": True}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "tool_required"
        assert response.json()["evidence"]["model_calls"] == 0
        assert response.json()["evidence"]["passages"] == []


@pytest.mark.parametrize(
    "mode", ["valid", "invalid_quote", "invalid_id", "invalid_usage", "refusal", "revoked", "timeout", "incomplete"]
)
async def test_one_synthesis_call_validates_quotes_and_rechecks_access(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    identifier = await indexed(client, "Payment testing is incomplete and blocks release.")
    monkeypatch.setenv("EVIDENCE__API_KEY", "test-only-not-real")
    monkeypatch.setenv("EVIDENCE__MODEL", "test-configured-model")
    get_settings.cache_clear()
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        assert body["store"] is False and body["max_output_tokens"] == 800
        assert "tools" not in body and request.url == "https://api.openai.com/v1/responses"
        entries = json.loads(body["input"])["evidence"]
        assert len(entries) <= 6
        if mode == "timeout":
            raise httpx.ReadTimeout("not logged")
        if mode == "revoked":
            async with get_session_factory().begin() as db:
                source = await db.get(KnowledgeSource, identifier)
                assert source
                source.is_active = False
        draft = {
            "status": "answered",
            "claims": [
                {
                    "text": "Payment testing blocks release.",
                    "citations": [
                        {
                            "passage_id": "invented" if mode == "invalid_id" else entries[0]["id"],
                            "quote": "invented quote"
                            if mode == "invalid_quote"
                            else "Payment testing is incomplete and blocks release.",
                        }
                    ],
                }
            ],
            "limitations": ["Parsed snapshot only."],
        }
        content = (
            [{"type": "refusal", "refusal": "Refused"}]
            if mode == "refusal"
            else [{"type": "output_text", "text": json.dumps(draft)}]
        )
        return httpx.Response(
            200,
            json={
                "status": "incomplete" if mode == "incomplete" else "completed",
                "output": [{"type": "message", "content": content}],
                "usage": {"input_tokens": -1 if mode == "invalid_usage" else 300, "output_tokens": 45},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "backend.services.evidence.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    response = await client.post(
        "/api/evidence/answer",
        json={"question": "Why is payment testing blocking release?", "project": "Atlas", "synthesize": True},
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    result = response.json()
    if mode == "valid":
        assert result["status"] == "answered" and result["claims"]
        assert result["input_tokens"] == 300 and result["output_tokens"] == 45
    elif mode == "revoked":
        assert result["status"] == "insufficient_evidence" and not result["evidence"]["passages"]
        assert "Payment testing" not in result["text"]
    else:
        assert result["status"] == "generation_unavailable"
        assert result["evidence"]["model_calls"] == 1


async def test_representative_retrieval_quality_and_latency(client: httpx.AsyncClient) -> None:
    examples = [
        (
            "# Release blockers\n\nThe Atlas release is delayed because payment testing is incomplete.",
            "Why is the Atlas release delayed?",
            "payment testing",
        ),
        (
            "# Operations\n\nThe worker retries transient connection failures using exponential backoff.",
            "How does the worker retry connection failures?",
            "exponential backoff",
        ),
        (
            "# Security\n\nPermission leases expire after 30 minutes. "
            "Revoked accounts cannot read private source content.",
            "What prevents revoked accounts from reading private source content?",
            "Revoked accounts",
        ),
        (
            "# Metrics\n\n| Operation | Duration (ms) |\n| --- | --- |\n| Parsing | 120 |\n| Search | 25 |",
            "What is the search duration?",
            "25",
        ),
    ]
    for body, _, _ in examples:
        await indexed(client, body)
    for i in range(16):
        await indexed(client, f"# Unrelated {i}\n\nOffice lunch menu item {i} includes lentils and rice.")
    samples: list[float] = []
    correct = 0
    evidence_counts: list[int] = []
    for _ in range(5):
        for _, question, expected in examples:
            started = time.perf_counter()
            response = await client.post("/api/evidence/answer", json={"question": question, "project": "Atlas"})
            samples.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200, response.text
            result = response.json()
            correct += int(any(expected in h["passage"]["content"] for h in result["evidence"]["passages"]))
            evidence_counts.append(result["evidence"]["evidence_tokens"])
            assert result["evidence"]["model_calls"] == 0
    assert correct == 20
    print(
        "EVIDENCE_BENCHMARK="
        + json.dumps(
            {
                "corpus_sources": 20,
                "requests": 20,
                "evidence_recall_at_6": correct / 20,
                "median_ms": round(statistics.median(samples), 2),
                "p95_ms": round(sorted(samples)[18], 2),
                "mean_evidence_tokens": round(statistics.mean(evidence_counts), 1),
                "external_api_calls": 0,
                "external_api_cost_usd": 0,
                "scope": (
                    "Local ASGI + real PostgreSQL; synthetic fixture set; "
                    "excludes Clerk, deployed network and live synthesis"
                ),
            }
        )
    )
