"""Shared Google revocation uses mocked HTTP and real owner-scoped PostgreSQL state."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import EnvelopeFactory
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.http import ProviderHttp
from backend.connectors.vault import TokenVault
from backend.db.models import (
    AgentRun,
    ConnectorAccount,
    ConnectorAudit,
    ConnectorCredential,
    ConnectorSubscription,
    ConnectorSyncJob,
    IngestionArtifact,
    KnowledgeSource,
)
from backend.db.session import get_session_factory
from backend.repositories.parsed import parsed_source
from backend.schemas.connectors import Credentials, FetchResult, Provider, Resource, Selection
from backend.services.connector_sources import register
from backend.services.connector_sync import process_job
from backend.services.ingestion import ingest_source
from backend.tests.test_connectors import BODY, config, connector_environment

__all__ = ["config", "connector_environment"]
pytestmark = pytest.mark.anyio


async def seed_connection(
    db: AsyncSession, settings: ConnectorConfig, provider: Provider,
    *, owner: str = "user_test", subject: str = "shared-google-subject",
) -> tuple[ConnectorAccount, KnowledgeSource, UUID]:
    reference = await TokenVault(settings).store(db, owner, Credentials(access_token=SecretStr("private-test-token")))
    account = ConnectorAccount(
        id=uuid4(), owner_id=owner, provider=provider, external_account_id=subject, display_name=provider,
        credential_reference=reference, status="connected", authentication_status="valid",
        configuration=Selection(resource_ids=["root"]).model_dump(mode="json"),
    )
    db.add(account)
    await db.flush()
    envelope = EnvelopeFactory(account.id, owner, provider, subject).text(
        Resource(id="guide", name="Guide", kind="document"), BODY, provider + ".txt", "text/plain"
    )
    await register(db, account, envelope)
    source = await db.get(KnowledgeSource, envelope.source_id)
    assert source is not None
    db.add(ConnectorSyncJob(account_id=account.id, mode="initial", status="running", cursor={}))
    db.add(ConnectorSubscription(
        id=uuid4(), account_id=account.id, resource_id="native-watch", token_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    ))
    await db.flush()
    return account, source, reference


@pytest.mark.parametrize("provider,companion_provider", [("gmail", "google_drive"), ("google_drive", "gmail")])
@pytest.mark.parametrize("policy,revocation_fails", [
    ("retain", False), ("soft_delete", False), ("purge", False), ("retain", True),
])
async def test_google_disconnect_disables_companion_before_remote_revocation(
    provider: Provider, companion_provider: Provider, policy: str, revocation_fails: bool,
    client: httpx.AsyncClient, db: AsyncSession, connector_environment: None,
    config: ConnectorConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    account, selected_source, _ = await seed_connection(db, config, provider)
    companion, companion_source, companion_reference = await seed_connection(db, config, companion_provider)
    unrelated = [
        await seed_connection(db, config, companion_provider, subject="different-google-subject"),
        await seed_connection(db, config, companion_provider, owner="user_other"),
        await seed_connection(db, config, "slack"),
    ]
    run = AgentRun(
        owner_id="user_test", request={}, scope={"accounts": [{"id": str(companion.id)}]},
        model="test-model", status="running", run_token=uuid4(), pending_calls=[{"name": "pending-read"}],
    )
    historical = AgentRun(
        owner_id="user_test", request={}, scope={"accounts": [{"id": str(companion.id)}]},
        model="test-model", status="completed", answer={"summary": "Preserved historical finding"},
    )
    db.add_all([run, historical])
    await db.commit()
    for source in (selected_source, companion_source):
        await ingest_source(source.id)
    assert companion_source.path is not None
    companion_path = Path(companion_source.path)
    async with get_session_factory()() as check:
        assert await parsed_source(check, companion_source.id, "user_test") is not None

    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST" and request.url.path.endswith(("/stop", "/revoke"))
        # The very first provider request must see all local access already disabled.
        async with get_session_factory()() as check:
            live = await check.get(ConnectorAccount, companion.id)
            credential = await check.get(ConnectorCredential, companion_reference)
            active_run = await check.get(AgentRun, run.id)
            assert live and live.status == "reauth_required" and live.credential_reference is None
            assert credential and credential.revoked_at and credential.ciphertext == ""
            assert active_run and active_run.status == "cancelled" and active_run.run_token is None
            assert not active_run.pending_calls
            assert await parsed_source(check, companion_source.id, "user_test") is None
        return httpx.Response(503 if revocation_fails and request.url.path == "/revoke" else 200, content=b"")

    original = ProviderHttp.__init__

    def initialize(
        self: ProviderHttp, settings: ConnectorConfig, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        original(self, settings, transport or httpx.MockTransport(respond))

    monkeypatch.setattr(ProviderHttp, "__init__", initialize)
    result = await client.post(f"/api/connectors/{account.id}/disconnect", json={"policy": policy})
    assert result.status_code == 200 and result.json()["status"] == "disconnected"
    assert len([request for request in requests if request.url.path == "/revoke"]) == (3 if revocation_fails else 1)
    if revocation_fails:
        assert "Remote revocation could not be confirmed" in result.json()["error_message"]
    status = await client.get(f"/api/connectors/{companion.id}/status")
    assert status.json()["status"] == "reauth_required" and "share authorization" in status.json()["error_message"]
    assert status.json()["authentication_status"] == "revoked" and status.json()["sync_status"] == "cancelled"
    async with get_session_factory()() as check:
        retained_source = await check.get(KnowledgeSource, companion_source.id)
        assert retained_source and not retained_source.is_active and retained_source.access_expires_at is not None
        assert retained_source.access_expires_at <= datetime.now(UTC)
        assert retained_source.path == str(companion_path) and companion_path.is_file()
        assert await check.get(IngestionArtifact, retained_source.id) is not None
        preserved = await check.get(AgentRun, historical.id)
        assert preserved and preserved.status == "completed" and preserved.answer == historical.answer
        subscription = await check.scalar(
            select(ConnectorSubscription).where(ConnectorSubscription.account_id == companion.id)
        )
        assert subscription is None
        job = await check.scalar(select(ConnectorSyncJob).where(ConnectorSyncJob.account_id == companion.id))
        assert job and job.status == "cancelled" and not await process_job(job.id)
        audit = await check.scalar(select(ConnectorAudit).where(
            ConnectorAudit.account_id == companion.id, ConnectorAudit.event == "shared_google_authorization_disabled"
        ))
        assert audit and audit.details["disconnected_account_id"] == str(account.id)
        for other, other_source, other_reference in unrelated:
            live = await check.get(ConnectorAccount, other.id)
            accessible = await check.get(KnowledgeSource, other_source.id)
            assert live and live.status == "connected" and live.credential_reference == other_reference
            assert accessible and accessible.is_active
        selected = await check.get(KnowledgeSource, selected_source.id)
        if policy == "purge":
            assert selected is None
        else:
            assert selected and not selected.is_active


async def test_simultaneous_google_disconnects_do_not_deadlock(
    client: httpx.AsyncClient, db: AsyncSession, connector_environment: None,
    config: ConnectorConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail, _, _ = await seed_connection(db, config, "gmail")
    drive, _, _ = await seed_connection(db, config, "google_drive")
    await db.commit()
    monkeypatch.setattr(
        "backend.connectors.base.ProviderHttp.request",
        http_request_success,
    )
    results = await asyncio.wait_for(asyncio.gather(
        client.post(f"/api/connectors/{gmail.id}/disconnect", json={"policy": "retain"}),
        client.post(f"/api/connectors/{drive.id}/disconnect", json={"policy": "retain"}),
    ), timeout=10)
    assert all(result.status_code == 200 and result.json()["status"] == "disconnected" for result in results)


async def http_request_success(self: ProviderHttp, method: str, url: str, **kwargs: object) -> bytes:
    return b""


async def test_companion_fetch_cannot_restore_sources_after_google_disconnect(
    client: httpx.AsyncClient, db: AsyncSession, connector_environment: None,
    config: ConnectorConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail, _, _ = await seed_connection(db, config, "gmail")
    drive, source, _ = await seed_connection(db, config, "google_drive")
    job = await db.scalar(select(ConnectorSyncJob).where(ConnectorSyncJob.account_id == drive.id))
    assert job
    resource = Resource(id="guide", name="Guide", kind="document")
    job.cursor = {
        "selection": drive.configuration, "verified": True, "verified_at": datetime.now(UTC).isoformat(),
        "pending": [resource.model_dump(mode="json")], "state": {},
    }
    job.progress = {"discovered": 1, "queued": 0, "unchanged": 0, "failed": 0, "removed": 0}
    await db.commit()
    started, release = asyncio.Event(), asyncio.Event()

    async def fetched(self: GoogleDriveConnector, ref: Resource, selection: Selection) -> FetchResult:
        started.set()
        await release.wait()
        factory = EnvelopeFactory(drive.id, drive.owner_id, "google_drive", drive.external_account_id)
        return FetchResult(envelopes=[factory.text(
            ref, BODY + " Changed after the fetch started.", "google_drive.txt", "text/plain"
        )])

    monkeypatch.setattr(GoogleDriveConnector, "fetch_resource", fetched)
    monkeypatch.setattr("backend.connectors.base.ProviderHttp.request", http_request_success)
    worker = asyncio.create_task(process_job(job.id))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        result = await client.post(f"/api/connectors/{gmail.id}/disconnect", json={"policy": "retain"})
        assert result.status_code == 200
    finally:
        release.set()
        await asyncio.wait_for(worker, timeout=5)
    async with get_session_factory()() as check:
        current = await check.get(KnowledgeSource, source.id)
        current_job = await check.get(ConnectorSyncJob, job.id)
        assert current and not current.is_active and current.file_hash == source.file_hash
        assert current_job and current_job.status == "cancelled"
