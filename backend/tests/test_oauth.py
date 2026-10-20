"""OAuth errors, one-shot token requests and durable refresh failures with mocked HTTP."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.base import OAuthConnector
from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.http import ProviderHttp
from backend.connectors.oauth import TOKEN_ENDPOINTS, OAuthOperation
from backend.connectors.slack import SlackConnector
from backend.connectors.vault import TokenVault
from backend.db.models import (
    AgentRun,
    ConnectorAccount,
    ConnectorAudit,
    ConnectorCredential,
    ConnectorSyncJob,
    KnowledgeSource,
)
from backend.db.session import close_database, get_session_factory
from backend.schemas.connectors import Credentials, Provider
from backend.tests.test_connectors import config, connector_environment
from backend.tests.test_google_disconnect import seed_connection

__all__ = ["config", "connector_environment"]
pytestmark = pytest.mark.anyio
CLASSES: dict[Provider, type[OAuthConnector]] = {
    "gmail": GmailConnector, "google_drive": GoogleDriveConnector, "github": GitHubConnector, "slack": SlackConnector,
}


async def token_request(connector: OAuthConnector, operation: OAuthOperation) -> Credentials:
    if operation == "exchange":
        return await connector.exchange("private-code", "private-verifier")
    return await connector.refresh(Credentials(
        access_token=SecretStr("expired-token"), refresh_token=SecretStr("private-refresh-token"),
        scopes=list(connector.scopes),
    ))


@pytest.mark.parametrize("provider,error,status,operation,expected", [
    ("gmail", "invalid_client", 400, "exchange", "oauth_client_invalid"),
    ("google_drive", "invalid_client", 401, "refresh", "oauth_client_invalid"),
    ("github", "incorrect_client_credentials", 200, "exchange", "oauth_client_invalid"),
    ("slack", "bad_client_secret", 200, "exchange", "oauth_client_invalid"),
    ("slack", "bad_redirect_uri", 200, "exchange", "oauth_redirect_mismatch"),
    ("gmail", "redirect_uri_mismatch", 400, "exchange", "oauth_redirect_mismatch"),
    ("github", "bad_verification_code", 200, "exchange", "oauth_code_invalid"),
    ("slack", "invalid_code", 200, "exchange", "oauth_code_invalid"),
    ("gmail", "invalid_grant", 400, "exchange", "oauth_code_invalid"),
    ("google_drive", "invalid_grant", 400, "refresh", "reauth_required"),
    ("github", "bad_refresh_token", 200, "refresh", "reauth_required"),
    ("slack", "invalid_refresh_token", 200, "refresh", "reauth_required"),
    ("slack", "invalid_scope", 200, "exchange", "oauth_scope_invalid"),
    ("gmail", "access_denied", 400, "exchange", "oauth_access_denied"),
    ("slack", "internal_error", 200, "refresh", "oauth_refresh_uncertain"),
    ("github", "private-provider-error", 200, "exchange", "oauth_authorization_failed"),
])
async def test_oauth_errors_preserve_safe_categories(
    provider: Provider, error: str, status: int, operation: OAuthOperation, expected: str, config: ConnectorConfig,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": error, "error_description": "private-provider-value"})

    connector = CLASSES[provider](config, http=ProviderHttp(config, httpx.MockTransport(respond)))
    with pytest.raises(ConnectorError) as failure:
        await token_request(connector, operation)
    assert failure.value.code == expected and len(requests) == 1
    assert "private-provider" not in str(failure.value)


@pytest.mark.parametrize("provider", ["gmail", "google_drive", "github", "slack"])
@pytest.mark.parametrize("operation", ["exchange", "refresh"])
@pytest.mark.parametrize("fault", ["timeout", "protocol", "server_error", "invalid_json", "missing_token"])
async def test_ambiguous_token_responses_never_retry(
    provider: Provider, operation: OAuthOperation, fault: str, config: ConnectorConfig,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("private-provider-value", request=request)
        if fault == "protocol":
            raise httpx.RemoteProtocolError("private-provider-value", request=request)
        if fault == "server_error":
            return httpx.Response(503, json={"secret": "private-provider-value"})
        if fault == "invalid_json":
            return httpx.Response(200, content=b"private-provider-value")
        return httpx.Response(200, json={"ok": True})

    connector = CLASSES[provider](config, http=ProviderHttp(config, httpx.MockTransport(respond)))
    with pytest.raises(ConnectorError) as failure:
        await token_request(connector, operation)
    assert failure.value.code == f"oauth_{operation}_uncertain" and len(requests) == 1
    assert "private-provider-value" not in str(failure.value)
    assert failure.value.retry_after == 0


@pytest.mark.parametrize("endpoint", sorted(TOKEN_ENDPOINTS))
async def test_transport_never_retries_token_endpoints_even_without_explicit_context(
    endpoint: str, config: ConnectorConfig,
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with pytest.raises(ConnectorError):
        await ProviderHttp(config, httpx.MockTransport(respond)).request("POST", endpoint)
    assert calls == 1


async def test_explicit_refresh_rate_limit_preserves_the_existing_credential(
    db: AsyncSession, connector_environment: None, config: ConnectorConfig,
) -> None:
    account, source, reference = await seed_connection(db, config, "github")
    await TokenVault(config).rotate(db, account.owner_id, reference, Credentials(
        access_token=SecretStr("expired-token"), refresh_token=SecretStr("private-refresh-token"),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    ))
    await db.commit()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "90"})

    connector = GitHubConnector(config, account, ProviderHttp(config, httpx.MockTransport(respond)))
    with pytest.raises(ConnectorError) as failure:
        await connector.access_token()
    assert failure.value.code == "rate_limited" and failure.value.retry_after == 90 and len(requests) == 1
    async with get_session_factory()() as check:
        live = await check.get(ConnectorAccount, account.id)
        credential = await check.get(ConnectorCredential, reference)
        original_source = await check.get(KnowledgeSource, source.id)
        assert live and live.status == "connected" and live.credential_reference == reference
        assert credential and credential.ciphertext and credential.revoked_at is None
        assert original_source and original_source.is_active


@pytest.mark.parametrize("provider", ["gmail", "google_drive", "github", "slack"])
@pytest.mark.parametrize("fault,expected", [
    ("timeout", "oauth_refresh_uncertain"), ("invalid_client", "oauth_client_invalid"),
])
async def test_refresh_failure_is_committed_and_cannot_reuse_the_old_token(
    provider: Provider, fault: str, expected: str, db: AsyncSession,
    connector_environment: None, config: ConnectorConfig,
) -> None:
    account, source, reference = await seed_connection(db, config, provider)
    await TokenVault(config).rotate(db, account.owner_id, reference, Credentials(
        access_token=SecretStr("expired-token"), refresh_token=SecretStr("private-refresh-token"),
        expires_at=datetime.now(UTC) - timedelta(seconds=1), scopes=list(CLASSES[provider].scopes),
    ))
    run = AgentRun(
        owner_id=account.owner_id, request={}, scope={"accounts": [{"id": str(account.id)}]},
        model="test-model", status="running", run_token=uuid4(),
    )
    db.add(run)
    await db.commit()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("private-provider-value", request=request)
        return httpx.Response(400, json={"error": "invalid_client", "error_description": "private-provider-value"})

    http = ProviderHttp(config, httpx.MockTransport(respond))
    with pytest.raises(ConnectorError) as failure:
        await CLASSES[provider](config, account, http).access_token()
    assert failure.value.code == expected and len(requests) == 1
    await close_database()
    with pytest.raises(ConnectorError) as next_attempt:
        await CLASSES[provider](config, account, http).access_token()
    assert next_attempt.value.code == "reauth_required" and len(requests) == 1
    async with get_session_factory()() as check:
        live = await check.get(ConnectorAccount, account.id)
        credential = await check.get(ConnectorCredential, reference)
        disabled_source = await check.get(KnowledgeSource, source.id)
        cancelled_run = await check.get(AgentRun, run.id)
        job = await check.scalar(select(ConnectorSyncJob).where(ConnectorSyncJob.account_id == account.id))
        assert live and live.status == "reauth_required" and live.credential_reference is None
        assert credential and credential.ciphertext == "" and credential.revoked_at is not None
        assert disabled_source and not disabled_source.is_active
        assert cancelled_run and cancelled_run.status == "cancelled" and cancelled_run.run_token is None
        assert job and job.status == "failed"
        audit = await check.scalar(select(ConnectorAudit).where(ConnectorAudit.event == "token_refresh_failed"))
        assert audit and audit.details == {"code": expected}
        assert "private-provider-value" not in json.dumps(audit.details)


@pytest.mark.parametrize("error,expected", [
    ("invalid_client", "OAUTH_CLIENT_INVALID"), ("invalid_grant", "OAUTH_CODE_INVALID"),
    ("redirect_uri_mismatch", "OAUTH_REDIRECT_MISMATCH"), ("timeout", "OAUTH_EXCHANGE_UNCERTAIN"),
])
async def test_failed_callback_retains_error_category_and_consumes_state_once(
    error: str, expected: str, client: httpx.AsyncClient, connector_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/token"
        if error == "timeout":
            raise httpx.ReadTimeout("private-provider-value", request=request)
        return httpx.Response(400, json={"error": error, "error_description": "private-provider-value"})

    original = ProviderHttp.__init__

    def initialize(
        self: ProviderHttp, settings: ConnectorConfig, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        original(self, settings, transport or httpx.MockTransport(respond))

    monkeypatch.setattr(ProviderHttp, "__init__", initialize)
    start = await client.post("/api/connectors/gmail/connect")
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    for attempt in range(2):
        response = await client.get("/api/connectors/gmail/callback", params={"state": state, "code": "private-code"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == (expected if attempt == 0 else "INVALID_STATE")
        assert "private-provider-value" not in response.text
    assert len(requests) == 1
    async with get_session_factory()() as check:
        audit = await check.scalar(select(ConnectorAudit).where(ConnectorAudit.event == "authorization_failed"))
        assert audit and audit.details == {"provider": "gmail", "code": expected.lower()}
