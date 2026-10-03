"""Connector contracts, real PostgreSQL lifecycle and mocked native HTTP boundaries."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError, EnvelopeFactory, record
from backend.connectors.events import signed
from backend.connectors.gmail import GmailConnector
from backend.connectors.http import ProviderHttp
from backend.connectors.rates import reserve
from backend.connectors.vault import TokenVault
from backend.db.models import (
    ConnectorAccount,
    ConnectorCredential,
    ConnectorEvent,
    ConnectorOAuthState,
    ConnectorResource,
    ConnectorSyncJob,
    DocumentChunk,
    IngestionArtifact,
    KnowledgeSource,
)
from backend.db.session import get_session_factory
from backend.repositories.retrieval import search_chunks
from backend.schemas.connectors import AccessPolicy, Credentials, RawSourceEnvelope, Resource, Selection, SyncPage
from backend.services.connector_sources import asset_path, register
from backend.services.connector_sync import process_job
from backend.services.ingestion import ingest_source

pytestmark = pytest.mark.anyio
VAULT_KEY = Fernet.generate_key().decode()
APP_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
BODY = (
    "PostgreSQL connection pools require spare capacity for background ingestion. "
    "When the pool is exhausted, configure a bounded worker count and monitor waiting connections. "
    "This troubleshooting reference describes connection timeout handling and database availability."
)


@pytest.fixture
def config() -> ConnectorConfig:
    return ConnectorConfig(
        vault_key=SecretStr(VAULT_KEY),
        google_client_id="test-google-client",
        google_client_secret=SecretStr("test-google-secret"),
        github_client_id="test-github-client",
        github_client_secret=SecretStr("test-github-secret"),
        github_app_id="7",
        github_private_key=SecretStr(APP_PRIVATE_KEY),
        slack_client_id="test-slack-client",
        slack_client_secret=SecretStr("test-slack-secret"),
        slack_signing_secret=SecretStr("test-slack-signature"),
        github_webhook_secret=SecretStr("test-github-signature"),
    )


@pytest.fixture
def connector_environment(database: None, monkeypatch: pytest.MonkeyPatch, config: ConnectorConfig) -> None:
    monkeypatch.setenv(
        "CONNECTORS",
        config.model_dump_json(
            exclude={
                "vault_key",
                "google_client_secret",
                "github_client_secret",
                "github_private_key",
                "slack_client_secret",
                "slack_signing_secret",
                "github_webhook_secret",
            }
        ),
    )
    for field in (
        "vault_key",
        "google_client_secret",
        "github_client_secret",
        "github_private_key",
        "slack_client_secret",
        "slack_signing_secret",
        "github_webhook_secret",
    ):
        value = getattr(config, field)
        monkeypatch.setenv("CONNECTORS__" + field.upper(), value.get_secret_value())
    get_settings.cache_clear()


def message(identifier: str = "m1", history: str = "12") -> dict[str, object]:
    return {
        "id": identifier,
        "threadId": "t1",
        "historyId": history,
        "labelIds": ["INBOX"],
        "internalDate": "1760000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Pool troubleshooting"},
                {"name": "From", "value": "sender@example.test"},
            ],
            "body": {"data": base64.urlsafe_b64encode(BODY.encode()).decode()},
        },
    }


@pytest.fixture
def gmail_http(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/token":
            values = parse_qs(request.content.decode())
            assert "client_secret" in values
            if values.get("grant_type") == ["authorization_code"]:
                assert len(values["code_verifier"][0]) >= 43
            return httpx.Response(
                200,
                json={
                    "access_token": "private-provider-token",
                    "refresh_token": "private-refresh-token",
                    "expires_in": 3600,
                    "scope": " ".join(GmailConnector.scopes),
                },
            )
        if request.url.path == "/v1/userinfo":
            return httpx.Response(200, json={"sub": "google-account-1", "email": "person@example.test"})
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "person@example.test", "historyId": "10"})
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"labels": [{"id": "INBOX", "name": "Inbox"}]})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
        if request.url.path.endswith("/messages/m1"):
            return httpx.Response(200, json=message())
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"historyId": "13", "history": []})
        if request.url.path == "/revoke" or request.url.path.endswith("/stop"):
            return httpx.Response(200, content=b"")
        raise AssertionError("Unexpected mocked provider endpoint " + request.url.path)

    original = ProviderHttp.__init__

    def initialize(
        self: ProviderHttp, settings: ConnectorConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        original(self, settings, transport or httpx.MockTransport(respond))

    monkeypatch.setattr(ProviderHttp, "__init__", initialize)
    return requests


async def connect_gmail(client: httpx.AsyncClient) -> str:
    created = await client.post("/api/connectors/gmail/connect")
    assert created.status_code == 200, created.text
    state = parse_qs(urlsplit(created.json()["authorization_url"]).query)["state"][0]
    result = await client.get("/api/connectors/gmail/callback", params={"state": state, "code": "authorization-code"})
    assert result.status_code == 200, result.text
    return str(result.json()["id"])


async def run_jobs(identifier: str, expected_status: str = "complete") -> None:
    async with get_session_factory()() as db:
        job_id = await db.scalar(
            select(ConnectorSyncJob.id)
            .where(ConnectorSyncJob.account_id == UUID(identifier))
            .order_by(ConnectorSyncJob.created_at.desc())
        )
    assert job_id
    for _ in range(30):
        await process_job(job_id)
        async with get_session_factory()() as db:
            job = await db.get(ConnectorSyncJob, job_id)
            assert job
            if job.status not in {"pending", "running", "waiting"}:
                assert job.status == expected_status, job.error_message
                return
    raise AssertionError("Sync failed to finish within bounded test steps")


async def test_oauth_state_is_owned_one_time_and_encrypted(
    client: httpx.AsyncClient, db: AsyncSession, connector_environment: None, gmail_http: list[httpx.Request]
) -> None:
    start = await client.post("/api/connectors/gmail/connect")
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    row = await db.get(ConnectorOAuthState, hashlib.sha256(state.encode()).hexdigest())
    assert row and row.verifier_encrypted and state not in row.verifier_encrypted
    wrong = await client.get(
        "/api/connectors/gmail/callback",
        params={"state": state, "code": "code"},
        headers={"X-FixFlow-User-Id": "user_other"},
    )
    assert wrong.status_code == 422
    result = await client.get("/api/connectors/gmail/callback", params={"state": state, "code": "code"})
    assert result.status_code == 200
    assert "private-provider-token" not in result.text and "credential_reference" not in result.text
    replay = await client.get("/api/connectors/gmail/callback", params={"state": state, "code": "code"})
    assert replay.status_code == 422
    credential = await db.scalar(select(ConnectorCredential))
    assert (
        credential
        and "private-provider-token" not in credential.ciphertext
        and "private-refresh-token" not in credential.ciphertext
    )


async def test_connection_selection_sync_ingestion_acl_and_disconnect(
    client: httpx.AsyncClient, connector_environment: None, gmail_http: list[httpx.Request]
) -> None:
    identifier = await connect_gmail(client)
    foreign = await client.get(f"/api/connectors/{identifier}/resources", headers={"X-FixFlow-User-Id": "user_other"})
    assert foreign.status_code == 404
    discovery = await client.get(f"/api/connectors/{identifier}/resources")
    assert discovery.json()["resources"][0]["id"] == "INBOX"
    assert (
        await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["not-owned"]})
    ).status_code == 422
    assert (
        await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["INBOX"]})
    ).status_code == 200
    for _ in range(2):
        assert (await client.post(f"/api/connectors/{identifier}/sync", json={})).status_code == 202
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncJob)) == 1
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        source = await db.scalar(select(KnowledgeSource))
        assert source and source.status == "uploaded"
        source_id = source.id
    await ingest_source(source_id)
    async with get_session_factory()() as db:
        db.info["owner_id"] = "user_test"
        assert await search_chunks(db, "PostgreSQL connection pools")
        artifact = await db.get(IngestionArtifact, source_id)
        assert (
            artifact
            and record(
                record(record(record(artifact.result["canonical"])["metadata"])["source_envelope"])["permissions"]
            )["application_owner"]
            == "user_test"
        )
        chunk = await db.scalar(select(DocumentChunk))
        assert chunk and record(record(chunk.meta["source_context"])["permissions"])["application_owner"] == "user_test"
        assert "private-provider-token" not in json.dumps(artifact.result)
        from backend.services.connector_sources import refresh_artifact_permissions  # noqa: PLC0415

        context = record(source.ingestion_metadata["source_envelope"])
        context["permissions"] = {**record(context["permissions"]), "users": ["new-reader@example.test"]}
        await refresh_artifact_permissions(db, source_id, context)
        await db.flush()
        await db.refresh(chunk)
        assert record(record(chunk.meta["source_context"])["permissions"])["users"] == ["new-reader@example.test"]
        db.info["owner_id"] = "user_other"
        assert not await search_chunks(db, "PostgreSQL connection pools")
    assert (await client.post(f"/api/connectors/{identifier}/sync", json={})).status_code == 202
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == 1
        await db.execute(update(KnowledgeSource).values(access_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
        db.info["owner_id"] = "user_test"
        assert not await search_chunks(db, "PostgreSQL connection pools")
    result = await client.post(f"/api/connectors/{identifier}/disconnect", json={"policy": "retain"})
    assert result.status_code == 200 and result.json()["status"] == "disconnected"
    async with get_session_factory()() as db:
        account = await db.get(ConnectorAccount, UUID(identifier))
        assert account and account.credential_reference is None
        source = await db.get(KnowledgeSource, source_id)
        assert source and not source.is_active
        credential = await db.scalar(select(ConnectorCredential))
        assert credential and credential.revoked_at


async def test_vault_rotation_owner_filter_and_revocation(db: AsyncSession, config: ConnectorConfig) -> None:
    vault = TokenVault(config)
    reference = await vault.store(db, "user_test", Credentials(access_token=SecretStr("first")))
    assert (await vault.retrieve(db, "user_test", reference)).access_token.get_secret_value() == "first"
    with pytest.raises(ConnectorError):
        await vault.retrieve(db, "user_other", reference)
    await vault.rotate(db, "user_test", reference, Credentials(access_token=SecretStr("second")))
    rotated = TokenVault(
        config.model_copy(
            update={"vault_key": SecretStr(Fernet.generate_key().decode()), "vault_previous_keys": [config.vault_key]}
        )
    )
    assert (await rotated.retrieve(db, "user_test", reference)).access_token.get_secret_value() == "second"
    await rotated.revoke(db, "user_test", reference)
    with pytest.raises(ConnectorError):
        await rotated.retrieve(db, "user_test", reference)


async def test_refresh_uses_server_vault(
    client: httpx.AsyncClient, connector_environment: None, gmail_http: list[httpx.Request]
) -> None:
    identifier = UUID(await connect_gmail(client))
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, identifier)
        assert account and account.credential_reference
        vault = TokenVault(get_settings().connectors)
        credentials = await vault.retrieve(db, account.owner_id, account.credential_reference)
        await vault.rotate(
            db,
            account.owner_id,
            account.credential_reference,
            credentials.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}),
        )
    assert (await client.get(f"/api/connectors/{identifier}/resources")).status_code == 200
    assert any(b"grant_type=refresh_token" in request.content for request in gmail_http if request.method == "POST")


async def test_identical_bytes_keep_distinct_provider_identities(db: AsyncSession, connector_environment: None) -> None:
    account = ConnectorAccount(
        id=uuid4(), owner_id="user_test", provider="gmail", external_account_id="external", display_name="Inbox"
    )
    db.add(account)
    await db.flush()
    factory = EnvelopeFactory(account.id, "user_test", "gmail", "external")
    for identifier in ("message-1", "message-2", "message-1"):
        await register(
            db,
            account,
            factory.text(
                Resource(id=identifier, name=identifier, kind="message", version="1"), BODY, "message.txt", "text/plain"
            ),
        )
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == 2
    assert await db.scalar(select(func.count()).select_from(ConnectorResource)) == 2


async def test_shared_rate_reservation_survives_requests(db: AsyncSession) -> None:
    await reserve("method", 60)
    with pytest.raises(ConnectorError, match="limit") as error:
        await reserve("method", 60)
    assert error.value.retry_after > 0


async def test_http_rate_retry_safe_errors_and_url_guard(config: ConnectorConfig) -> None:
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts < 3 else 200, json={"ok": True})

    http = ProviderHttp(config, httpx.MockTransport(respond))
    assert await http.request("GET", "https://api.github.com/user") == {"ok": True}
    assert attempts == 3
    with pytest.raises(ConnectorError, match="authorized"):
        await http.request("GET", "https://127.0.0.1/private")
    limited = ProviderHttp(
        config,
        httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "90"}, json={"secret": "never-return"})
        ),
    )
    with pytest.raises(ConnectorError) as error:
        await limited.request("GET", "https://api.github.com/user")
    assert error.value.retry_after == 90 and "never-return" not in str(error.value)
    revoked = ProviderHttp(
        config, httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "invalid_grant"}))
    )
    with pytest.raises(ConnectorError) as error:
        await revoked.request("POST", "https://oauth2.googleapis.com/token")
    assert error.value.code == "reauth_required"
    nonfinite = ProviderHttp(
        config, httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"invalid": NaN}'))
    )
    with pytest.raises(ConnectorError) as error:
        await nonfinite.request("GET", "https://api.github.com/user")
    assert error.value.code == "invalid_response"


async def test_event_signatures_tamper_timestamp_and_replay(
    client: httpx.AsyncClient, connector_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONNECTORS__EVENTS_ENABLED", "true")
    get_settings.cache_clear()
    payload = json.dumps(
        {"type": "event_callback", "event_id": "e1", "team_id": "T1", "event": {"type": "message"}}
    ).encode()
    stamp = str(int(time.time()))
    signature = (
        "v0=" + hmac.new(b"test-slack-signature", b"v0:" + stamp.encode() + b":" + payload, hashlib.sha256).hexdigest()
    )
    headers = {"X-Slack-Signature": signature, "X-Slack-Request-Timestamp": stamp}
    invalid = await client.post("/webhooks/connectors/slack", content=payload + b" ", headers=headers)
    assert invalid.status_code == 401
    valid = await client.post("/webhooks/connectors/slack", content=payload, headers=headers)
    assert valid.status_code == 200 and not valid.json()["duplicate"]
    replay = await client.post("/webhooks/connectors/slack", content=payload, headers=headers)
    assert replay.status_code == 200 and replay.json()["duplicate"]
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorEvent)) == 1
    with pytest.raises(ConnectorError):
        signed(
            "slack",
            payload,
            {**{k.lower(): v for k, v in headers.items()}, "x-slack-request-timestamp": "1"},
            get_settings().connectors,
        )


async def test_contract_and_asset_validation(config: ConnectorConfig) -> None:
    with pytest.raises(ValidationError):
        Selection(resource_ids=["same", "same"])
    with pytest.raises(ConnectorError):
        asset_path("upload:../../private.env")
    with pytest.raises(ValidationError):
        RawSourceEnvelope(
            source_id=uuid4(),
            connector_account_id=uuid4(),
            provider="gmail",
            external_id="x",
            external_version="1",
            resource_type="email",
            filename="x.txt",
            mime_type="text/plain",
            content=None,
            content_hash="0" * 64,
            permissions=AccessPolicy(application_owner="user_test", provider_resource="x"),
        )


async def test_rate_limit_checkpoint_resumes_without_restart(
    client: httpx.AsyncClient,
    connector_environment: None,
    gmail_http: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = await connect_gmail(client)
    await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["INBOX"]})
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    async with get_session_factory()() as db:
        job_id = await db.scalar(select(ConnectorSyncJob.id))
    assert job_id
    await process_job(job_id)
    original = GmailConnector.fetch_resource
    attempts = 0

    async def limited(self: GmailConnector, resource: Resource, selection: Selection):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectorError("rate_limited", "Provider rate limit reached; synchronization will resume", 90)
        return await original(self, resource, selection)

    monkeypatch.setattr(GmailConnector, "fetch_resource", limited)
    await process_job(job_id)
    async with get_session_factory().begin() as db:
        job = await db.get(ConnectorSyncJob, job_id)
        assert job and job.status == "waiting"
        assert record(job.cursor["pending"][0])["id"] == "m1"  # type: ignore[index]
        job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    await run_jobs(identifier)
    assert attempts == 2
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == 1


async def test_deleted_resource_is_removed_before_retrieval(
    client: httpx.AsyncClient,
    connector_environment: None,
    gmail_http: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock  # noqa: PLC0415

    identifier = await connect_gmail(client)
    await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["INBOX"]})
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier)
    monkeypatch.setattr(
        GmailConnector,
        "incremental_sync",
        AsyncMock(return_value=SyncPage(deleted_ids=["m1"], complete=True, checkpoint={"history_id": "14"})),
    )
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        source = await db.scalar(select(KnowledgeSource))
        resource = await db.scalar(select(ConnectorResource))
        assert source and not source.is_active
        assert resource and resource.removed_at
    response = await client.get("/api/sources")
    assert response.json()[0]["is_active"] is False


@pytest.mark.parametrize("policy", ["retain", "soft_delete", "purge"])
async def test_disconnect_content_policies(
    policy: str, client: httpx.AsyncClient, connector_environment: None, gmail_http: list[httpx.Request]
) -> None:
    from pathlib import Path  # noqa: PLC0415

    identifier = await connect_gmail(client)
    await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["INBOX"]})
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        source = await db.scalar(select(KnowledgeSource))
        assert source and source.path
        path = Path(source.path)
    result = await client.post(f"/api/connectors/{identifier}/disconnect", json={"policy": policy})
    assert result.status_code == 200
    async with get_session_factory()() as db:
        source = await db.scalar(select(KnowledgeSource))
        resource = await db.scalar(select(ConnectorResource))
        if policy == "purge":
            assert source is None and resource is None and not path.exists()
            job = await db.scalar(select(ConnectorSyncJob))
            assert job and job.cursor == {}
        else:
            assert resource
            assert source and not source.is_active and path.exists()
            assert (resource.removed_at is not None) == (policy == "soft_delete")


async def test_disconnect_still_disables_access_when_vault_key_is_missing(
    client: httpx.AsyncClient,
    connector_environment: None,
    gmail_http: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = await connect_gmail(client)
    monkeypatch.setenv("CONNECTORS__VAULT_KEY", "")
    get_settings.cache_clear()
    result = await client.post(f"/api/connectors/{identifier}/disconnect", json={"policy": "retain"})
    assert result.status_code == 200 and result.json()["status"] == "disconnected"
    assert "could not be confirmed" in result.json()["error_message"]
    async with get_session_factory()() as db:
        credential = await db.scalar(select(ConnectorCredential))
        assert credential and credential.revoked_at and credential.ciphertext == ""


async def test_github_replay_cannot_change_unsigned_delivery_identity(
    client: httpx.AsyncClient, connector_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONNECTORS__EVENTS_ENABLED", "true")
    get_settings.cache_clear()
    payload = b'{"action":"ping"}'
    signature = "sha256=" + hmac.new(b"test-github-signature", payload, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Delivery": "delivery-1"}
    rejected = await client.post("/webhooks/connectors/github", content=payload + b" ", headers=headers)
    assert rejected.status_code == 401
    accepted = await client.post("/webhooks/connectors/github", content=payload, headers=headers)
    assert accepted.status_code == 200 and not accepted.json()["duplicate"]
    replay = await client.post(
        "/webhooks/connectors/github", content=payload, headers={**headers, "X-GitHub-Delivery": "forged-delivery"}
    )
    assert replay.status_code == 200 and replay.json()["duplicate"]


async def test_pubsub_identity_requires_signature_audience_and_verified_sender(
    config: ConnectorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jwt  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

    from backend.connectors.events import google_identity  # noqa: PLC0415

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "test-key"
    monkeypatch.setattr("backend.connectors.events._jwks", (time.monotonic() + 300, [public_jwk]))
    configured = config.model_copy(update={"gmail_pubsub_service_account": "push@example.iam.gserviceaccount.com"})
    claims = {
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "iss": "https://accounts.google.com",
        "aud": configured.public_url + "/api/connectors/events/gmail",
        "email": configured.gmail_pubsub_service_account,
        "email_verified": True,
    }

    def token(changes: Mapping[str, object]) -> str:
        return jwt.encode({**claims, **changes}, private_key, algorithm="RS256", headers={"kid": "test-key"})

    await google_identity({"authorization": "Bearer " + token({})}, configured)
    unknown = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "unknown-key"})
    with pytest.raises(ConnectorError):
        await google_identity({"authorization": "Bearer " + unknown}, configured)
    # The invalid key ID must not evict trusted keys or cause a network request.
    await google_identity({"authorization": "Bearer " + token({})}, configured)
    for changes in (
        {"aud": "https://attacker.test"},
        {"iss": "https://attacker.test"},
        {"email": "other@example.iam.gserviceaccount.com"},
        {"email_verified": False},
        {"exp": int(time.time()) - 1},
    ):
        with pytest.raises(ConnectorError, match="identity"):
            await google_identity({"authorization": "Bearer " + token(changes)}, configured)
    with pytest.raises(ConnectorError):
        await google_identity({"authorization": "Bearer unsigned"}, configured)


async def test_drive_channel_token_expiry_and_replay(
    client: httpx.AsyncClient, db: AsyncSession, connector_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.db.models import ConnectorSubscription  # noqa: PLC0415

    monkeypatch.setenv("CONNECTORS__EVENTS_ENABLED", "true")
    get_settings.cache_clear()
    account = ConnectorAccount(
        owner_id="user_test", provider="google_drive", external_account_id="drive-user", display_name="Drive"
    )
    db.add(account)
    await db.flush()
    subscription = ConnectorSubscription(
        account_id=account.id,
        resource_id="remote-channel",
        token_hash=hashlib.sha256(b"channel-secret").hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(subscription)
    await db.commit()
    headers = {
        "X-Goog-Channel-Id": str(subscription.id),
        "X-Goog-Channel-Token": "channel-secret",
        "X-Goog-Resource-Id": "remote-channel",
        "X-Goog-Message-Number": "1",
    }
    rejected = await client.post(
        "/webhooks/connectors/google_drive", headers={**headers, "X-Goog-Channel-Token": "wrong"}
    )
    assert rejected.status_code == 401
    accepted = await client.post("/webhooks/connectors/google_drive", headers=headers)
    assert accepted.status_code == 200 and not accepted.json()["duplicate"]
    replay = await client.post("/webhooks/connectors/google_drive", headers=headers)
    assert replay.status_code == 200 and replay.json()["duplicate"]
    subscription.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    expired = await client.post("/webhooks/connectors/google_drive", headers={**headers, "X-Goog-Message-Number": "2"})
    assert expired.status_code == 401


async def test_completed_incremental_inventory_removes_missing_files(
    db: AsyncSession, connector_environment: None
) -> None:
    from backend.services.connector_sync import finish  # noqa: PLC0415

    account = ConnectorAccount(
        id=uuid4(), owner_id="user_test", provider="github", external_account_id="github-user", display_name="GitHub"
    )
    db.add(account)
    await db.flush()
    factory = EnvelopeFactory(account.id, "user_test", "github", account.external_account_id)
    for identifier in ("old.py", "current.py", "another-repo.py"):
        group = "repository:1:code:main" if identifier != "another-repo.py" else "repository:2:code:main"
        await register(
            db,
            account,
            factory.text(
                Resource(
                    id=identifier, name=identifier, kind="source_file", version="1", metadata={"sync_group": group}
                ),
                BODY,
                identifier + ".txt",
                "text/plain",
            ),
        )
    started = datetime.now(UTC)
    await db.execute(
        update(ConnectorResource)
        .where(ConnectorResource.external_id == "current.py")
        .values(last_seen_at=started + timedelta(seconds=1))
    )
    job = ConnectorSyncJob(account_id=account.id, mode="incremental", status="running")
    db.add(job)
    await db.commit()
    progress = {"discovered": 1, "queued": 0, "unchanged": 1, "removed": 0, "failed": 0}
    await finish(
        job.id,
        account.id,
        {"checkpoint": {}, "inventory_groups": ["repository:1:code:main"]},
        progress,
        "incremental",
        started,
    )
    rows = list(await db.scalars(select(ConnectorResource).execution_options(populate_existing=True)))
    assert {row.external_id for row in rows if row.removed_at} == {"old.py"}
    sources = list(await db.scalars(select(KnowledgeSource).execution_options(populate_existing=True)))
    assert sum(source.is_active for source in sources) == 2


async def test_cached_drive_download_restriction_is_authoritative(
    db: AsyncSession, connector_environment: None
) -> None:
    from backend.services.connector_sources import reusable_file  # noqa: PLC0415

    account = ConnectorAccount(
        id=uuid4(),
        owner_id="user_test",
        provider="google_drive",
        external_account_id="drive-user",
        display_name="Drive",
    )
    with pytest.raises(ConnectorError, match="disabled downloads"):
        await reusable_file(
            db,
            account,
            Resource(
                id="file:guide",
                name="guide.txt",
                kind="drive_file",
                version="same-version",
                metadata={"can_download": False},
            ),
        )


async def test_disconnect_does_not_refresh_tokens_before_disabling_local_access(
    client: httpx.AsyncClient,
    connector_environment: None,
    gmail_http: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock  # noqa: PLC0415

    identifier = await connect_gmail(client)
    refresh = AsyncMock(side_effect=AssertionError("Disconnect must not refresh against an unavailable provider"))
    monkeypatch.setattr(GmailConnector, "access_token", refresh)
    result = await client.post(f"/api/connectors/{identifier}/disconnect", json={"policy": "retain"})
    assert result.status_code == 200 and result.json()["status"] == "disconnected"
    refresh.assert_not_called()


async def test_reconnect_forces_initial_sync_of_retained_content(
    client: httpx.AsyncClient,
    connector_environment: None,
    gmail_http: list[httpx.Request],
) -> None:
    identifier = await connect_gmail(client)
    await client.post(f"/api/connectors/{identifier}/configure", json={"resource_ids": ["INBOX"]})
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier)
    await client.post(f"/api/connectors/{identifier}/disconnect", json={"policy": "retain"})
    reconnected = await connect_gmail(client)
    assert reconnected == identifier
    async with get_session_factory()() as db:
        account = await db.get(ConnectorAccount, UUID(identifier))
        assert account and account.sync_cursor == {}
        source = await db.scalar(select(KnowledgeSource))
        assert source and not source.is_active
    await client.post(f"/api/connectors/{identifier}/sync", json={})
    await run_jobs(identifier)
    async with get_session_factory()() as db:
        source = await db.scalar(select(KnowledgeSource))
        assert source and source.is_active
        job = await db.scalar(select(ConnectorSyncJob).order_by(ConnectorSyncJob.created_at.desc()).limit(1))
        assert job and job.mode == "initial"


async def test_cached_permission_evidence_rebuilds_users_and_groups(
    db: AsyncSession,
    connector_environment: None,
) -> None:
    from backend.services.connector_sources import reusable_file  # noqa: PLC0415

    account = ConnectorAccount(
        id=uuid4(),
        owner_id="user_test",
        provider="google_drive",
        external_account_id="drive-user",
        display_name="Drive",
    )
    db.add(account)
    await db.flush()
    old: list[dict[str, object]] = [
        {"id": "person", "type": "user", "role": "reader", "emailAddress": "old@example.test"}
    ]
    ref = Resource(
        id="file:guide", name="guide.txt", kind="drive_file", version="1", metadata={"provider_permissions": old}
    )
    await register(
        db,
        account,
        EnvelopeFactory(account.id, account.owner_id, "google_drive", "drive-user").text(
            ref,
            BODY,
            "guide.txt",
            "text/plain",
            permissions=old,
        ),
    )
    await db.commit()
    updated = ref.model_copy(
        update={
            "metadata": {
                "provider_permissions": [
                    {
                        "id": "group",
                        "type": "group",
                        "role": "reader",
                        "emailAddress": "team@example.test",
                    }
                ]
            }
        }
    )
    cached = await reusable_file(db, account, updated)
    assert cached and cached.permissions.users == [] and cached.permissions.groups == ["team@example.test"]
    assert cached.permissions.application_owner == account.owner_id
