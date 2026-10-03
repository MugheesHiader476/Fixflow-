"""Real adapters, persistence and parsing with mocked HTTP and an accelerated quota clock."""

import base64
import json
from datetime import UTC, datetime, timedelta, tzinfo
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import EnvelopeFactory, record
from backend.connectors.events import lifecycle_event
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.http import ProviderHttp
from backend.connectors.slack import SlackConnector
from backend.connectors.vault import TokenVault
from backend.db.models import (
    ConnectorAccount,
    ConnectorCredential,
    ConnectorResource,
    ConnectorSyncJob,
    IngestionArtifact,
    KnowledgeSource,
)
from backend.db.session import get_session_factory
from backend.repositories.retrieval import search_chunks
from backend.schemas.connectors import Credentials, Resource
from backend.services.connector_sources import register
from backend.services.ingestion import ingest_source
from backend.tests.test_connectors import (
    APP_PRIVATE_KEY,
    BODY,
    config,
    connect_gmail,
    connector_environment,
    message,
    run_jobs,
)

# Make the shared pytest fixtures available to this module.
__all__ = ["config", "connector_environment"]

pytestmark = pytest.mark.anyio


class QuotaClock:
    """Advance native quota windows without blocking the workflow test for minutes."""

    offset = 0

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        cls.offset += 61
        return datetime.now(tz) + timedelta(seconds=cls.offset)


class NativeFixture:
    def __init__(self, provider: str) -> None:
        self.provider, self.removed = provider, False
        self.requests: list[httpx.Request] = []
        self.repo: dict[str, object] = {"id": 1, "full_name": "owner/docs", "default_branch": "main", "private": True}
        self.file: dict[str, object] = {
            "id": "guide1",
            "name": "guide.txt",
            "mimeType": "text/plain",
            "version": "1",
            "parents": ["root"],
            "capabilities": {"canDownload": True},
            "permissions": [{"type": "user", "role": "owner", "id": "person"}],
        }
        self.channel = {"id": "C1", "name": "docs", "is_private": True, "is_member": True}
        self.folder: dict[str, object] = {
            "id": "root",
            "name": "docs",
            "mimeType": "application/vnd.google-apps.folder",
        }

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in {"/token", "/login/oauth/access_token", "/api/oauth.v2.access"}:
            tokens = {
                "access_token": "private-provider-token",
                "refresh_token": "private-refresh-token",
                "expires_in": 3600,
            }
            if self.provider == "slack":
                return httpx.Response(
                    200, json={"ok": True, "authed_user": {**tokens, "scope": ",".join(SlackConnector.scopes)}}
                )
            scopes = (
                GmailConnector.scopes
                if self.provider == "gmail"
                else ["https://www.googleapis.com/auth/drive.readonly"]
            )
            return httpx.Response(200, json={**tokens, "scope": " ".join(scopes)})
        if path == "/v1/userinfo":
            return httpx.Response(200, json={"sub": "google-user", "email": "person@example.test"})
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "person@example.test", "historyId": "10"})
        if path.endswith("/labels"):
            return httpx.Response(200, json={"labels": [{"id": "INBOX", "name": "Inbox"}]})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [] if self.removed else [{"id": "m1", "threadId": "t1"}]})
        if path.endswith("/messages/m1"):
            return httpx.Response(404) if self.removed else httpx.Response(200, json=message())
        if path.endswith("/history"):
            return httpx.Response(
                200,
                json={
                    "historyId": "13",
                    "history": [
                        {
                            "messagesDeleted": [{"message": {"id": "m1", "threadId": "t1"}}],
                        }
                    ]
                    if self.removed
                    else [],
                },
            )
        if path.endswith("/about"):
            return httpx.Response(200, json={"user": {"emailAddress": "person@example.test"}})
        if path.endswith("/drives"):
            return httpx.Response(200, json={"drives": []})
        if path.endswith("/files"):
            files = [] if self.removed else [self.file]
            if "in parents" not in request.url.params.get("q", ""):
                files = [self.folder, *files]
            return httpx.Response(200, json={"files": files})
        if path.endswith("/files/root"):
            return httpx.Response(200, json=self.folder)
        if path.endswith("/files/guide1"):
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=BODY.encode())
            return httpx.Response(200, json={**self.file, "trashed": self.removed})
        if path.endswith("/startPageToken"):
            return httpx.Response(200, json={"startPageToken": "drive-anchor"})
        if path.endswith("/changes"):
            return httpx.Response(
                200,
                json={
                    "changes": [{"fileId": "guide1", "removed": True}] if self.removed else [],
                    "newStartPageToken": "drive-next",
                },
            )
        if path == "/user":
            return httpx.Response(200, json={"id": 7, "login": "person"})
        if path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [{"id": 9, "app_id": 7}]})
        if path == "/user/installations/9/repositories":
            return httpx.Response(200, json={"total_count": 1, "repositories": [self.repo]})
        if path == "/repositories/1":
            return httpx.Response(200, json=self.repo)
        if path.endswith("/installation"):
            return httpx.Response(200, json={"id": 9, "app_id": 7})
        if path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "private-installation-token"})
        if path.endswith("/branches/main"):
            return httpx.Response(200, json={"commit": {"sha": "head2" if self.removed else "head1"}})
        if "/git/trees/" in path:
            return httpx.Response(
                200,
                json={
                    "tree": [] if self.removed else [{"type": "blob", "sha": "blob1", "path": "guide.txt"}],
                },
            )
        if path.endswith("/git/blobs/blob1"):
            return httpx.Response(200, json={"content": base64.b64encode(BODY.encode()).decode()})
        if path.endswith("/auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T1", "team": "Documentation", "user_id": "U1"})
        if path.endswith("/conversations.list"):
            return httpx.Response(200, json={"ok": True, "channels": [self.channel]})
        if path.endswith("/conversations.info"):
            return httpx.Response(200, json={"ok": True, "channel": self.channel})
        if path.endswith("/conversations.history"):
            absent = self.removed or float(request.url.params.get("oldest", "0")) > 1760000000.000001
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": []
                    if absent
                    else [
                        {
                            "type": "message",
                            "ts": "1760000000.000001",
                            "text": BODY,
                            "user": "U1",
                        }
                    ],
                    "has_more": False,
                },
            )
        if path.endswith("/users.info"):
            return httpx.Response(200, json={"ok": True, "user": {"id": "U1", "name": "person"}})
        if path == "/revoke" or path.endswith("/stop"):
            return httpx.Response(200, content=b"")
        if path.endswith("/auth.revoke"):
            return httpx.Response(200, json={"ok": True})
        if path.startswith("/applications/") and request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(path)


@pytest.mark.parametrize("provider", ["gmail", "google_drive", "github", "slack"])
async def test_native_provider_workflow_through_real_pipeline(
    provider: str,
    client: httpx.AsyncClient,
    connector_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = NativeFixture(provider)
    original = ProviderHttp.__init__

    def initialize(
        self: ProviderHttp, settings: ConnectorConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        original(self, settings, transport or httpx.MockTransport(native.respond))

    monkeypatch.setattr(ProviderHttp, "__init__", initialize)
    monkeypatch.setattr("backend.connectors.rates.datetime", QuotaClock)
    if provider == "gmail":
        identifier = await connect_gmail(client)
    else:
        created = await client.post(f"/api/connectors/{provider}/connect")
        assert created.status_code == 200
        state = parse_qs(urlsplit(created.json()["authorization_url"]).query)["state"][0]
        connected = await client.get(
            f"/api/connectors/{provider}/callback", params={"state": state, "code": "test-code"}
        )
        assert connected.status_code == 200, connected.text
        identifier = connected.json()["id"]
    discovery = await client.get(f"/api/connectors/{identifier}/resources")
    assert discovery.status_code == 200
    if provider == "google_drive":
        discovery = await client.get(
            f"/api/connectors/{identifier}/resources", params={"cursor": discovery.json()["next_cursor"]}
        )
    resource_id = discovery.json()["resources"][0]["id"]
    selected = await client.post(
        f"/api/connectors/{identifier}/configure", json={"resource_ids": [resource_id], "categories": ["code"]}
    )
    assert selected.status_code == 200, selected.text
    assert (await client.post(f"/api/connectors/{identifier}/sync", json={})).status_code == 202
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        sources = list(await db.scalars(select(KnowledgeSource)))
        assert sources and all(
            record(source.ingestion_metadata["source_envelope"])["provider"] == provider for source in sources
        )
        source_ids = [source.id for source in sources]
        if provider == "github":
            connection = await db.get(ConnectorAccount, UUID(identifier))
            assert connection and connection.meta["installations"] == {"1": 9}
    for source_id in source_ids:
        await ingest_source(source_id)
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        assert await search_chunks(db, "PostgreSQL connection pools")
        artifacts = list(await db.scalars(select(IngestionArtifact)))
        assert artifacts and all("private-provider-token" not in json.dumps(row.result) for row in artifacts)
    assert (await client.post(f"/api/connectors/{identifier}/sync", json={})).status_code == 202
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == len(sources)
    native.removed = True
    # Timestamp polling cannot discover old Slack deletions; full inventory can.
    mode = "reconcile" if provider in {"slack", "google_drive"} else "incremental"
    assert (await client.post(f"/api/connectors/{identifier}/sync", json={"mode": mode})).status_code == 202
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        assert not await search_chunks(db, "PostgreSQL connection pools")
    disconnected = await client.post(f"/api/connectors/{identifier}/disconnect", json={"policy": "purge"})
    assert disconnected.status_code == 200 and disconnected.json()["authentication_status"] == "revoked"
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectorResource)) == 0
    assert any(
        request.method == "DELETE" or "/revoke" in request.url.path or "auth.revoke" in request.url.path
        for request in native.requests
    )
    if provider == "github":
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey  # noqa: PLC0415
        from cryptography.hazmat.primitives.serialization import load_pem_private_key  # noqa: PLC0415

        signing_key = load_pem_private_key(APP_PRIVATE_KEY.encode(), password=None)
        assert isinstance(signing_key, RSAPrivateKey)
        app_request = next(request for request in native.requests if request.url.path.endswith("/installation"))
        claims = jwt.decode(
            app_request.headers["authorization"].removeprefix("Bearer "),
            signing_key.public_key(),
            algorithms=["RS256"],
        )
        assert claims["iss"] == "7" and claims["exp"] > claims["iat"]


async def test_lost_label_reconciles_remaining_roots(
    client: httpx.AsyncClient,
    connector_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = NativeFixture("gmail")
    lost = False

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/labels"):
            labels = [{"id": "DOCS", "name": "Docs"}]
            if not lost:
                labels.append({"id": "INBOX", "name": "Inbox"})
            return httpx.Response(200, json={"labels": labels})
        if request.url.path.endswith("/messages"):
            identifier = "m2" if request.url.params["labelIds"] == "DOCS" else "m1"
            return httpx.Response(200, json={"messages": [{"id": identifier, "threadId": "t1"}]})
        if request.url.path.endswith("/messages/m2"):
            return httpx.Response(200, json={**message("m2"), "labelIds": ["DOCS"]})
        return native.respond(request)

    original = ProviderHttp.__init__

    def initialize(
        self: ProviderHttp, settings: ConnectorConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        original(self, settings, httpx.MockTransport(respond))

    monkeypatch.setattr(ProviderHttp, "__init__", initialize)
    identifier = await connect_gmail(client)
    assert (
        await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["INBOX", "DOCS"]})
    ).status_code == 200
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier)
    lost = True
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier, "complete_with_warning")
    async with get_session_factory()() as db:
        resources = list(await db.scalars(select(ConnectorResource)))
        assert {row.external_id for row in resources if row.removed_at} == {"m1"}
        sources = list(await db.scalars(select(KnowledgeSource)))
        assert sum(source.is_active for source in sources) == 1
    status = await client.get(f"/api/connectors/{identifier}/status")
    assert "Update your resource selection" in status.json()["error_message"]
    assert status.json()["configuration"]["resource_ids"] == ["INBOX", "DOCS"]


async def test_slack_revocation_only_wipes_the_affected_user(
    db: AsyncSession,
    connector_environment: None,
    config: ConnectorConfig,
) -> None:
    accounts = []
    for user in ("U1", "U2"):
        credential = await TokenVault(config).store(db, user, Credentials(access_token=SecretStr("private-user-token")))
        account = ConnectorAccount(
            id=uuid4(),
            owner_id=user,
            provider="slack",
            external_account_id="T1",
            display_name=user,
            credential_reference=credential,
            meta={"user_id": user},
            configuration={"resource_ids": ["C1"]},
        )
        db.add(account)
        await db.flush()
        await register(
            db,
            account,
            EnvelopeFactory(account.id, user, "slack", "T1").text(
                Resource(id="C1:1", name="Message", kind="slack_message", metadata={"channel_id": "C1"}),
                BODY,
                "message.md",
                "text/markdown",
            ),
        )
        db.add(ConnectorSyncJob(account_id=account.id, mode="initial", status="pending"))
        accounts.append(account)
    await db.flush()
    event: dict[str, object] = {"event": {"type": "tokens_revoked", "tokens": {"oauth": ["U1"], "bot": []}}}
    assert await lifecycle_event(db, "slack", accounts[0], event)
    assert not await lifecycle_event(db, "slack", accounts[1], event)
    await db.commit()
    assert accounts[0].status == "revoked" and accounts[0].credential_reference is None
    assert accounts[1].status == "connected" and accounts[1].credential_reference
    sources = list(await db.scalars(select(KnowledgeSource)))
    assert {source.owner_id for source in sources if source.is_active} == {"U2"}
    wiped = await db.scalar(select(ConnectorCredential).where(ConnectorCredential.owner_id == "U1"))
    assert wiped and wiped.ciphertext == "" and wiped.revoked_at
    jobs = list(await db.scalars(select(ConnectorSyncJob)))
    assert {job.account_id for job in jobs if job.status == "cancelled"} == {accounts[0].id}


async def test_github_installation_removal_disables_only_its_repositories(
    db: AsyncSession,
    connector_environment: None,
) -> None:
    account = ConnectorAccount(
        id=uuid4(),
        owner_id="user_test",
        provider="github",
        external_account_id="7",
        display_name="person",
        meta={"installations": {"1": 9, "2": 10}},
        configuration={"resource_ids": ["1", "2"]},
    )
    db.add(account)
    await db.flush()
    for repository in ("1", "2"):
        await register(
            db,
            account,
            EnvelopeFactory(account.id, account.owner_id, "github", "7").text(
                Resource(
                    id=repository + ":file", name="guide", kind="source_file", metadata={"repository_id": repository}
                ),
                BODY,
                "guide.txt",
                "text/plain",
            ),
        )
    job = ConnectorSyncJob(account_id=account.id, mode="initial", status="running")
    db.add(job)
    await db.flush()
    assert not await lifecycle_event(db, "github", account, {"action": "deleted", "installation": {"id": 9}})
    await db.commit()
    resources = list(await db.scalars(select(ConnectorResource)))
    assert {row.external_id for row in resources if row.removed_at} == {"1:file"}
    assert account.meta["reconcile_requested"]
    assert account.status == "connected"
    await db.refresh(job)
    assert job.status == "cancelled"


@pytest.mark.parametrize("provider", ["google_drive", "github", "slack"])
async def test_provider_refresh_rotates_encrypted_credentials(
    provider: str,
    db: AsyncSession,
    connector_environment: None,
    config: ConnectorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.connectors.drive import GoogleDriveConnector  # noqa: PLC0415

    cls = {"google_drive": GoogleDriveConnector, "github": GitHubConnector, "slack": SlackConnector}[provider]
    scopes = list(cls.scopes)

    def respond(request: httpx.Request) -> httpx.Response:
        assert parse_qs(request.content.decode())["grant_type"] == ["refresh_token"]
        assert parse_qs(request.content.decode())["refresh_token"] == ["old-refresh-token"]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "new-private-access-token",
                "refresh_token": "new-private-refresh-token",
                "expires_in": 3600,
                "scope": " ".join(scopes),
            },
        )

    reference = await TokenVault(config).store(
        db,
        "user_test",
        Credentials(
            access_token=SecretStr("old-access-token"),
            refresh_token=SecretStr("old-refresh-token"),
            scopes=scopes,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ),
    )
    account = ConnectorAccount(
        id=uuid4(),
        owner_id="user_test",
        provider=provider,
        external_account_id="external",
        display_name="person",
        credential_reference=reference,
    )
    db.add(account)
    await db.commit()
    connector = cls(config, account, ProviderHttp(config, httpx.MockTransport(respond)))
    assert await connector.access_token() == "new-private-access-token"
    credentials = await TokenVault(config).retrieve(db, account.owner_id, reference)
    assert credentials.refresh_token and credentials.refresh_token.get_secret_value() == "new-private-refresh-token"
    ciphertext = await db.get(ConnectorCredential, reference, populate_existing=True)
    assert ciphertext and "new-private" not in ciphertext.ciphertext
