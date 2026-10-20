"""Owner-scoped connector lifecycle, encrypted OAuth state and durable job scheduling."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError
from backend.connectors.github import GitHubConnector
from backend.connectors.registry import ADAPTERS, adapter, configured, missing_configuration, verify_selection
from backend.connectors.vault import TokenVault
from backend.db.models import (
    ConnectorAccount,
    ConnectorAudit,
    ConnectorOAuthState,
    ConnectorSubscription,
    ConnectorSyncJob,
    KnowledgeSource,
)
from backend.db.session import get_session_factory
from backend.repositories.agent_lifecycle import disable as disable_agent_workflows
from backend.schemas.connectors import (
    AccountResponse,
    ConnectionStatus,
    Provider,
    ProviderResponse,
    Selection,
    SyncStatus,
)
from backend.services.access import owner_id
from backend.services.uploads import discard_upload

ACTIVE = ("connected", "syncing", "connected_with_warning")
PENDING = ("pending", "running", "waiting")
logger = logging.getLogger(__name__)


def audit(db: AsyncSession, owner: str, event: str, account_id: UUID | None = None, **details: object) -> None:
    db.add(ConnectorAudit(owner_id=owner, account_id=account_id, event=event, details=details))
    logger.info("Connector event %s account=%s", event, account_id)


async def owned_account(db: AsyncSession, identifier: UUID, *, lock: bool = False) -> ConnectorAccount:
    query = select(ConnectorAccount).where(ConnectorAccount.id == identifier, ConnectorAccount.owner_id == owner_id(db))
    account = await db.scalar(query.with_for_update() if lock else query)
    if account is None:
        raise ConnectorError("not_found", "Connection not found")
    return account


async def response(db: AsyncSession, account: ConnectorAccount) -> AccountResponse:
    job = await db.scalar(
        select(ConnectorSyncJob)
        .where(ConnectorSyncJob.account_id == account.id)
        .order_by(ConnectorSyncJob.created_at.desc())
        .limit(1)
    )
    return AccountResponse(
        id=account.id,
        provider=cast(Provider, account.provider),
        display_name=account.display_name,
        external_account_id=account.external_account_id,
        status=cast(ConnectionStatus, account.status),
        authentication_status=account.authentication_status,
        provider_health=account.provider_health,
        sync_status=cast(SyncStatus, account.sync_status),
        last_successful_request=account.last_successful_request,
        last_sync_at=account.last_sync_at,
        error_message=account.error_message,
        configuration=Selection.model_validate(account.configuration),
        progress=job.progress if job else {},
        created_at=account.created_at,
    )


async def providers(db: AsyncSession) -> list[ProviderResponse]:
    config = get_settings().connectors
    accounts = list(
        await db.scalars(
            select(ConnectorAccount)
            .where(ConnectorAccount.owner_id == owner_id(db))
            .order_by(ConnectorAccount.created_at)
        )
    )
    result = []
    for provider in cast(tuple[Provider, ...], ("gmail", "github", "google_drive", "slack")):
        enabled = configured(provider, config)
        slug = config.github_app_slug
        install_url = (
            "https://github.com/apps/" + slug + "/installations/new"
            if provider == "github" and slug and all(c.isalnum() or c == "-" for c in slug)
            else None
        )
        result.append(
            ProviderResponse(
                provider=provider,
                configured=enabled,
                setup_message=(
                    None if enabled else
                    "Slack requires HTTPS. Set CONNECTORS__PUBLIC_URL=https://localhost:3000 on both servers "
                    "for local development, then register the HTTPS callback in Slack."
                    if provider == "slack" and not config.public_url.startswith("https://") else
                    "Server OAuth credentials and encryption key are required"
                ),
                accounts=[await response(db, account) for account in accounts if account.provider == provider],
                install_url=install_url,
                gmail_send_enabled=provider == "gmail" and config.gmail_send_enabled,
                callback_url=config.public_url + f"/api/connectors/{provider}/callback",
                required_scopes=list(ADAPTERS[provider](config, None).authorization_scopes),
                missing_configuration=missing_configuration(provider, config),
            )
        )
    return result


async def begin_connection(db: AsyncSession, provider: Provider) -> str:
    config = get_settings().connectors
    connector = adapter(provider, config)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    url = await connector.get_authorization_url(state, verifier)
    db.add(
        ConnectorOAuthState(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            owner_id=owner_id(db),
            provider=provider,
            verifier_encrypted=TokenVault(config).encrypt(verifier),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    audit(db, owner_id(db), "authorization_started", provider=provider)
    await db.commit()
    return url


async def callback(db: AsyncSession, provider: Provider, state: str, code: str) -> ConnectorAccount:
    config = get_settings().connectors
    connector = adapter(provider, config)
    vault = TokenVault(config)
    row = await db.scalar(
        select(ConnectorOAuthState)
        .where(
            ConnectorOAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest(),
            ConnectorOAuthState.owner_id == owner_id(db),
            ConnectorOAuthState.provider == provider,
        )
        .with_for_update()
    )
    if row is None or row.consumed_at or row.expires_at <= datetime.now(UTC):
        raise ConnectorError("invalid_state", "Authorization state is invalid or expired; start connection again")
    verifier = vault.decrypt(row.verifier_encrypted)
    row.consumed_at, row.verifier_encrypted = datetime.now(UTC), ""
    # Consume once before contacting a provider, including failed/exchanged callbacks.
    await db.commit()
    try:
        credentials, external, name, metadata = await connector.handle_callback(code, verifier)
    except ConnectorError as error:
        audit(db, owner_id(db), "authorization_failed", provider=provider, code=error.code)
        await db.commit()
        raise
    identity = f"{owner_id(db)}:{provider}:{external}".encode()
    lock_key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    account = await db.scalar(
        select(ConnectorAccount)
        .where(
            ConnectorAccount.owner_id == owner_id(db),
            ConnectorAccount.provider == provider,
            ConnectorAccount.external_account_id == external,
        )
        .with_for_update()
    )
    if account is None:
        account = ConnectorAccount(
            owner_id=owner_id(db), provider=provider, external_account_id=external, display_name=name, configuration={}
        )
        db.add(account)
        await db.flush()
    if account.credential_reference:
        await vault.revoke(db, account.owner_id, account.credential_reference)
    account.credential_reference = await vault.store(db, account.owner_id, credentials)
    account.display_name, account.scopes, account.meta = name, credentials.scopes, metadata
    account.status, account.authentication_status, account.provider_health = "connected", "valid", "available"
    account.error_message = None
    account.reconcile_at = datetime.now(UTC)
    # Reconnect never re-enables retained sources until authoritative synchronization succeeds.
    account.sync_cursor = {}
    await db.execute(
        update(KnowledgeSource)
        .where(KnowledgeSource.connector_account_id == account.id)
        .values(is_active=False, access_expires_at=datetime.now(UTC))
    )
    await db.execute(
        update(ConnectorSyncJob)
        .where(ConnectorSyncJob.account_id == account.id, ConnectorSyncJob.status.in_(PENDING))
        .values(status="cancelled")
    )
    account.sync_status = "idle"
    audit(db, account.owner_id, "connection_created", account.id, provider=provider)
    await db.commit()
    return account


async def configure(db: AsyncSession, identifier: UUID, selection: Selection) -> ConnectorAccount:
    account = await owned_account(db, identifier)
    if account.status not in ACTIVE:
        raise ConnectorError("reauth_required", "Reconnect this account before selecting resources")
    connector = adapter(cast(Provider, account.provider), get_settings().connectors, account)
    await verify_selection(connector, selection)
    await db.refresh(account, with_for_update=True)
    if account.status not in ACTIVE:
        raise ConnectorError("reauth_required", "Connection is no longer authorized")
    if isinstance(connector, GitHubConnector):
        account.meta = {**account.meta, "installations": connector.observed_installations}
    if account.configuration != selection.model_dump(mode="json"):
        await db.execute(
            update(ConnectorSyncJob)
            .where(ConnectorSyncJob.account_id == account.id, ConnectorSyncJob.status.in_(PENDING))
            .values(status="cancelled")
        )
        await db.execute(
            update(KnowledgeSource).where(KnowledgeSource.connector_account_id == account.id).values(is_active=False)
        )
        account.configuration, account.sync_cursor = selection.model_dump(mode="json"), {}
        account.meta = {k: v for k, v in account.meta.items() if k not in {"pending_events", "failed_refs"}}
        account.sync_status, account.reconcile_at = "idle", datetime.now(UTC)
    audit(db, account.owner_id, "selection_updated", account.id, resources=len(selection.resource_ids))
    await db.commit()
    return account


async def enqueue(db: AsyncSession, account: ConnectorAccount, mode: str = "incremental") -> ConnectorSyncJob:
    # Caller holds the account row; this serializes manual, event and scheduler requests.
    existing = await db.scalar(
        select(ConnectorSyncJob)
        .where(ConnectorSyncJob.account_id == account.id, ConnectorSyncJob.status.in_(PENDING))
        .limit(1)
    )
    if existing:
        if mode == "reconcile":
            account.meta = {**account.meta, "reconcile_requested": True}
        return existing
    if account.status not in ACTIVE:
        raise ConnectorError("reauth_required", "Reconnect before synchronizing")
    selection = Selection.model_validate(account.configuration)
    if not selection.resource_ids:
        raise ConnectorError("invalid_selection", "Select resources before synchronizing")
    failed = await db.scalar(
        select(ConnectorSyncJob)
        .where(ConnectorSyncJob.account_id == account.id, ConnectorSyncJob.status == "failed")
        .order_by(ConnectorSyncJob.created_at.desc())
        .limit(1)
    )
    if failed and mode == "incremental" and failed.cursor.get("selection") == selection.model_dump(mode="json"):
        failed.status, failed.available_at = "pending", datetime.now(UTC)
        failed.cursor = {**failed.cursor, "retries": 0, "verified": False}
        account.sync_status = "pending"
        return failed
    effective_mode = "initial" if not account.sync_cursor else mode
    job = ConnectorSyncJob(
        account_id=account.id,
        mode=effective_mode,
        cursor={
            "state": account.sync_cursor if effective_mode == "incremental" else {},
            "selection": selection.model_dump(mode="json"),
            "pending": account.meta.get("failed_refs", []) if effective_mode == "incremental" else [],
            "verified": False,
        },
        progress={"discovered": 0, "queued": 0, "unchanged": 0, "removed": 0, "failed": 0},
    )
    db.add(job)
    account.sync_status = "pending"
    audit(db, account.owner_id, "sync_queued", account.id, mode=effective_mode)
    await db.flush()
    return job


async def failure(identifier: UUID, error: ConnectorError) -> None:
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, identifier, with_for_update=True)
        if account is None or account.status == "disconnected":
            return
        account.error_message = str(error)[:500]
        if error.code in {"reauth_required", "missing_scope", "credential_unavailable", "oauth_refresh_uncertain"}:
            account.status, account.authentication_status = "reauth_required", "invalid"
            await db.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.connector_account_id == identifier)
                .values(is_active=False)
            )
            await db.execute(
                update(ConnectorSyncJob)
                .where(ConnectorSyncJob.account_id == identifier, ConnectorSyncJob.status.in_(PENDING))
                .values(status="failed")
            )
            account.sync_status = "failed"
        else:
            account.provider_health = "rate_limited" if error.code == "rate_limited" else "unavailable"
        audit(db, account.owner_id, "connector_failure", account.id, code=error.code)


async def invalidate_google_companions(db: AsyncSession, account: ConnectorAccount) -> None:
    """Google revokes the project grant, including the owner's matching Gmail/Drive connection."""
    companions = list(await db.scalars(
        select(ConnectorAccount)
        .where(
            ConnectorAccount.owner_id == account.owner_id,
            ConnectorAccount.external_account_id == account.external_account_id,
            ConnectorAccount.provider.in_(("gmail", "google_drive")),
            ConnectorAccount.id != account.id,
            ConnectorAccount.status != "disconnected",
        )
        .order_by(ConnectorAccount.id)
        .with_for_update()
    ))
    vault = TokenVault(get_settings().connectors)
    for companion in companions:
        if companion.credential_reference:
            await vault.revoke(db, companion.owner_id, companion.credential_reference)
        companion.credential_reference = None
        companion.status, companion.authentication_status = "reauth_required", "revoked"
        companion.sync_status, companion.provider_health = "cancelled", "unavailable"
        companion.error_message = (
            "A related Google app was disconnected. Gmail and Google Drive share authorization for this Google "
            "account; reconnect this app to continue. Stored content has been kept."
        )
        companion.meta = {k: v for k, v in companion.meta.items() if k not in {"pending_events", "failed_refs"}}
        await disable_agent_workflows(db, companion.id)
        await db.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.connector_account_id == companion.id)
            .values(is_active=False, access_expires_at=datetime.now(UTC))
        )
        await db.execute(
            update(ConnectorSyncJob)
            .where(ConnectorSyncJob.account_id == companion.id, ConnectorSyncJob.status.in_(PENDING))
            .values(status="cancelled")
        )
        await db.execute(delete(ConnectorSubscription).where(ConnectorSubscription.account_id == companion.id))
        audit(
            db, companion.owner_id, "shared_google_authorization_disabled", companion.id,
            disconnected_account_id=str(account.id),
        )


async def disconnect(db: AsyncSession, identifier: UUID, policy: str) -> ConnectorAccount:
    account = await owned_account(db, identifier)
    if account.provider in {"gmail", "google_drive"}:
        # Acquire the group lock before any account row lock, so simultaneous
        # Gmail/Drive disconnects cannot deadlock while invalidating each other.
        identity = f"google-disconnect:{account.owner_id}:{account.external_account_id}".encode()
        key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
        await db.execute(select(func.pg_advisory_xact_lock(key)))
    await db.refresh(account, with_for_update=True)
    warning = None
    connector = None
    token = None
    try:
        from backend.connectors.base import OAuthConnector  # noqa: PLC0415

        candidate = adapter(cast(Provider, account.provider), get_settings().connectors, account)
        if isinstance(candidate, OAuthConnector):
            connector = candidate
            if account.credential_reference:
                credentials = await TokenVault(get_settings().connectors).retrieve(
                    db, account.owner_id, account.credential_reference
                )
                token = credentials.access_token.get_secret_value()
            else:
                raise ConnectorError("credential_unavailable", "Connection credentials are unavailable")
    except ConnectorError:
        warning = (
            "Local access disabled. Remote revocation could not be confirmed; revoke the app in provider settings."
        )
    await db.refresh(account, with_for_update=True)
    if account.credential_reference:
        from backend.db.models import ConnectorCredential  # noqa: PLC0415

        await db.execute(
            update(ConnectorCredential)
            .where(
                ConnectorCredential.id == account.credential_reference, ConnectorCredential.owner_id == account.owner_id
            )
            .values(ciphertext="", revoked_at=datetime.now(UTC))
        )
    account.credential_reference = None
    account.status, account.authentication_status, account.sync_status = "disconnected", "revoked", "cancelled"
    account.error_message = warning
    account.meta = {**account.meta, "disconnected_policy": policy}
    await disable_agent_workflows(db, account.id, purge=policy == "purge")
    # Keep selection/account identity for reconnect; clear pending account payloads.
    account.meta = {k: v for k, v in account.meta.items() if k not in {"pending_events", "failed_refs"}}
    await db.execute(
        update(ConnectorSyncJob)
        .where(ConnectorSyncJob.account_id == account.id, ConnectorSyncJob.status.in_(PENDING))
        .values(status="cancelled")
    )
    paths: list[Path] = []
    if policy == "purge":
        from backend.db.models import ConnectorResource  # noqa: PLC0415

        rows = list(await db.scalars(select(KnowledgeSource).where(KnowledgeSource.connector_account_id == account.id)))
        paths = [Path(row.path) for row in rows if row.path]
        await db.execute(delete(KnowledgeSource).where(KnowledgeSource.connector_account_id == account.id))
        await db.execute(delete(ConnectorResource).where(ConnectorResource.account_id == account.id))
        await db.execute(update(ConnectorSyncJob).where(ConnectorSyncJob.account_id == account.id).values(cursor={}))
    else:
        await db.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.connector_account_id == account.id)
            .values(is_active=False, access_expires_at=datetime.now(UTC))
        )
        if policy == "soft_delete":
            from backend.db.models import ConnectorResource  # noqa: PLC0415

            await db.execute(
                update(ConnectorResource)
                .where(ConnectorResource.account_id == account.id)
                .values(removed_at=datetime.now(UTC))
            )
    audit(db, account.owner_id, "connection_revoked", account.id, policy=policy, local_access_disabled=True)
    if account.provider in {"gmail", "google_drive"} and connector and token:
        # Commit all affected access/credential changes before the provider can
        # revoke the shared grant, even if its response later fails or is lost.
        await invalidate_google_companions(db, account)
    await db.commit()
    for path in paths:
        if not path.is_symlink() and path.resolve().is_relative_to(get_settings().upload_dir.resolve()):
            discard_upload(path)
    # Retrieval and workers are already disabled while remote cleanup is attempted.
    if connector and token:
        from backend.connectors.subscriptions import stop_subscriptions  # noqa: PLC0415

        try:
            await stop_subscriptions(connector, account, token)
        except ConnectorError:
            warning = "Local access disabled. Provider notification cleanup could not be confirmed."
        try:
            await connector.revoke_token(token)
        except ConnectorError:
            warning = (
                "Local access disabled. Remote revocation could not be confirmed; revoke the app in provider settings."
            )
    await db.execute(delete(ConnectorSubscription).where(ConnectorSubscription.account_id == account.id))
    account.error_message = warning
    audit(db, account.owner_id, "remote_cleanup_completed", account.id, confirmed=warning is None)
    await db.commit()
    return account
