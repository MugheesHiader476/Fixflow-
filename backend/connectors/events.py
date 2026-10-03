"""Verify bounded event bytes before parsing; replay receipts and wake-ups commit together."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from uuid import UUID

import jwt
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError, record, records, string, strings, unbase64
from backend.connectors.http import ProviderHttp
from backend.connectors.registry import adapter
from backend.db.models import (
    ConnectorAccount,
    ConnectorCredential,
    ConnectorEvent,
    ConnectorResource,
    ConnectorSubscription,
    ConnectorSyncJob,
    KnowledgeSource,
)
from backend.schemas.connectors import Provider
from backend.services.connector_sources import remove
from backend.services.connectors import ACTIVE, PENDING, audit, enqueue

_jwks: tuple[float, list[dict[str, object]]] = (0, [])


async def request_reconciliation(db: AsyncSession, account: ConnectorAccount) -> None:
    account.meta = {**account.meta, "reconcile_requested": True}
    account.reconcile_at = datetime.now(UTC)
    # A fetched response from before the permission event must not reactivate
    # content after the denial commits. Replace the in-flight inventory.
    await db.execute(
        update(ConnectorSyncJob)
        .where(
            ConnectorSyncJob.account_id == account.id,
            ConnectorSyncJob.status.in_(PENDING),
        )
        .values(status="cancelled")
    )
    account.sync_status = "cancelled"


async def revoke_connection(db: AsyncSession, account: ConnectorAccount) -> None:
    if account.credential_reference:
        await db.execute(
            update(ConnectorCredential)
            .where(
                ConnectorCredential.id == account.credential_reference,
                ConnectorCredential.owner_id == account.owner_id,
            )
            .values(ciphertext="", revoked_at=datetime.now(UTC))
        )
    account.credential_reference = None
    account.status, account.authentication_status, account.sync_status = "revoked", "invalid", "cancelled"
    account.error_message = "Provider authorization was revoked. Reconnect to continue."
    account.meta = {k: v for k, v in account.meta.items() if k not in {"pending_events", "failed_refs"}}
    await db.execute(
        update(ConnectorSyncJob)
        .where(
            ConnectorSyncJob.account_id == account.id,
            ConnectorSyncJob.status.in_(PENDING),
        )
        .values(status="cancelled")
    )
    await db.execute(
        update(KnowledgeSource)
        .where(
            KnowledgeSource.connector_account_id == account.id,
        )
        .values(is_active=False, access_expires_at=datetime.now(UTC))
    )


async def lifecycle_event(
    db: AsyncSession, provider: Provider, account: ConnectorAccount, event: dict[str, object]
) -> bool:
    """Verified provider events may immediately deny access; only a native probe can restore it."""
    if provider == "slack":
        item = record(event.get("event", {}))
        kind = item.get("type")
        if kind == "app_uninstalled" or (
            kind == "tokens_revoked"
            and account.meta.get("user_id") in strings(record(item.get("tokens", {})).get("oauth", []))
        ):
            await revoke_connection(db, account)
            return True
        channel = item.get("channel")
        if isinstance(channel, dict):
            channel = channel.get("id")
        lost_channel = kind in {"channel_deleted", "group_deleted"} or (
            kind == "member_left_channel" and item.get("user") == account.meta.get("user_id")
        )
        ids: list[str] = []
        if lost_channel and isinstance(channel, str):
            ids = list(
                await db.scalars(
                    select(ConnectorResource.external_id).where(
                        ConnectorResource.account_id == account.id,
                        ConnectorResource.meta["channel_id"].astext == channel,
                    )
                )
            )
        if kind in {"file_deleted", "file_unshared"}:
            identifier = item.get("file_id", item.get("file"))
            if isinstance(identifier, str):
                ids.extend(
                    await db.scalars(
                        select(ConnectorResource.external_id).where(
                            ConnectorResource.account_id == account.id,
                            ConnectorResource.meta["file_id"].astext == identifier,
                        )
                    )
                )
        if ids or (lost_channel and channel in strings(account.configuration.get("resource_ids", []))):
            await remove(db, account.id, ids)
            await request_reconciliation(db, account)
    elif provider == "github":
        if (
            event.get("action") == "revoked"
            and str(record(event.get("sender", {})).get("id")) == account.external_account_id
        ):
            await revoke_connection(db, account)
            return True
        removed = {str(repo.get("id")) for repo in records(event.get("repositories_removed", []))}
        installation_id = record(event.get("installation", {})).get("id")
        if event.get("action") in {"deleted", "suspend"} and installation_id is not None and "repository" not in event:
            removed.update(
                key
                for key, value in record(account.meta.get("installations", {})).items()
                if str(value) == str(installation_id)
            )
        if removed:
            ids = list(
                await db.scalars(
                    select(ConnectorResource.external_id).where(
                        ConnectorResource.account_id == account.id,
                        ConnectorResource.meta["repository_id"].astext.in_(removed),
                    )
                )
            )
            await remove(db, account.id, ids)
            await request_reconciliation(db, account)
    return False


def signed(provider: Provider, body: bytes, headers: dict[str, str], config: ConnectorConfig) -> None:
    if provider == "github":
        key = config.github_webhook_secret
        expected = (
            "sha256=" + hmac.new(key.get_secret_value().encode(), body, hashlib.sha256).hexdigest() if key else ""
        )
        signature = headers.get("x-hub-signature-256", "")
    elif provider == "slack":
        key = config.slack_signing_secret
        stamp = headers.get("x-slack-request-timestamp", "")
        if not stamp.isdigit() or abs(time.time() - int(stamp)) > 300:
            raise ConnectorError("invalid_event", "Event timestamp is invalid or expired")
        expected = (
            "v0="
            + hmac.new(
                key.get_secret_value().encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256
            ).hexdigest()
            if key
            else ""
        )
        signature = headers.get("x-slack-signature", "")
    else:
        raise ConnectorError("invalid_event", "Unknown signed event provider")
    if not expected or not signature.isascii() or not hmac.compare_digest(signature, expected):
        raise ConnectorError("invalid_event", "Event signature is invalid")


async def google_identity(headers: dict[str, str], config: ConnectorConfig) -> None:
    global _jwks
    header = headers.get("authorization", "")
    if not header.startswith("Bearer ") or not config.gmail_pubsub_service_account:
        raise ConnectorError("invalid_event", "Authenticated Pub/Sub delivery is required")
    token = header.removeprefix("Bearer ")
    try:
        unverified = jwt.get_unverified_header(token)
        if unverified.get("alg") != "RS256":
            raise jwt.InvalidTokenError()
        if _jwks[0] <= time.monotonic():
            payload = record(await ProviderHttp(config).request("GET", "https://www.googleapis.com/oauth2/v3/certs"))
            _jwks = (time.monotonic() + 300, records(payload["keys"]))
        key = next((key for key in _jwks[1] if key.get("kid") == unverified.get("kid")), None)
        if key is None:
            # Unverified key IDs must not evict the trusted cache and trigger
            # arbitrary outbound certificate requests. Rotation is bounded by its TTL.
            raise jwt.InvalidTokenError()
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key).key,
            algorithms=["RS256"],
            audience=config.public_url + "/api/connectors/events/gmail",
            issuer=["https://accounts.google.com", "accounts.google.com"],
            options={"require": ["exp", "iat", "iss", "aud", "email"]},
        )
        if claims.get("email") != config.gmail_pubsub_service_account or claims.get("email_verified") is not True:
            raise jwt.InvalidTokenError()
    except (jwt.PyJWTError, ValueError, TypeError) as error:
        raise ConnectorError("invalid_event", "Pub/Sub identity is invalid") from error


async def receive(
    db: AsyncSession, provider: Provider, body: bytes, headers: dict[str, str], config: ConnectorConfig
) -> dict[str, object]:
    if not config.events_enabled:
        raise ConnectorError("not_configured", "Connector notifications are disabled")
    accounts: list[ConnectorAccount] = []
    if provider in {"github", "slack"}:
        signed(provider, body, headers, config)
    elif provider == "gmail":
        await google_identity(headers, config)
    if provider == "google_drive":
        try:
            channel = UUID(headers.get("x-goog-channel-id", ""))
        except ValueError as error:
            raise ConnectorError("invalid_event", "Invalid Drive channel") from error
        subscription = await db.get(ConnectorSubscription, channel)
        token_hash = hashlib.sha256(headers.get("x-goog-channel-token", "").encode()).hexdigest()
        if (
            subscription is None
            or subscription.expires_at <= datetime.now(UTC)
            or not hmac.compare_digest(subscription.token_hash, token_hash)
            or subscription.resource_id != headers.get("x-goog-resource-id")
        ):
            raise ConnectorError("invalid_event", "Invalid or expired Drive notification")
        account = await db.get(ConnectorAccount, subscription.account_id, with_for_update=True)
        if account and account.provider == provider:
            accounts = [account]
        message = headers.get("x-goog-message-number", "")
        if not message.isdigit():
            raise ConnectorError("invalid_event", "Invalid Drive notification number")
        event_id = str(channel) + ":" + message
        event: dict[str, object] = {}
    else:
        try:
            event = record(json.loads(body))
        except (ValueError, RecursionError) as error:
            raise ConnectorError("invalid_event", "Invalid event payload") from error
        if provider == "slack" and event.get("type") == "url_verification":
            return {"challenge": string(event["challenge"])}
        if provider == "slack":
            event_id = string(event["event_id"])
            external = string(event["team_id"])
            accounts = list(
                await db.scalars(
                    select(ConnectorAccount)
                    .where(ConnectorAccount.provider == provider, ConnectorAccount.external_account_id == external)
                    .with_for_update()
                )
            )
        elif provider == "gmail":
            pubsub_message = record(event["message"])
            event_id = string(pubsub_message["messageId"])
            try:
                notification = record(json.loads(unbase64(pubsub_message["data"])))
            except (ValueError, RecursionError) as error:
                raise ConnectorError("invalid_event", "Invalid Gmail notification") from error
            email = string(notification["emailAddress"])
            accounts = list(
                await db.scalars(
                    select(ConnectorAccount)
                    .where(ConnectorAccount.provider == provider, ConnectorAccount.meta["email"].astext == email)
                    .with_for_update()
                )
            )
        else:
            event_id = headers.get("x-github-delivery", "")
            if not event_id:
                raise ConnectorError("invalid_event", "GitHub delivery identity is required")
            # GitHub signs the body, not the delivery header; changing that header must not bypass replay protection.
            event_id = hashlib.sha256(body).hexdigest()
            repo_id = str(record(event.get("repository", {})).get("id", ""))
            query = select(ConnectorAccount).where(ConnectorAccount.provider == provider)
            if repo_id:
                query = query.where(ConnectorAccount.configuration["resource_ids"].contains([repo_id]))
            accounts = list(await db.scalars(query.with_for_update()))
    if not event_id or len(event_id) > 300:
        raise ConnectorError("invalid_event", "Invalid event identity")
    created = await db.scalar(
        insert(ConnectorEvent)
        .values(provider=provider, external_event_id=event_id)
        .on_conflict_do_nothing(index_elements=["provider", "external_event_id"])
        .returning(ConnectorEvent.id)
    )
    if created is None:
        await db.rollback()
        return {"accepted": True, "duplicate": True}
    for account in accounts:
        if account.status not in ACTIVE:
            continue
        revoked = await lifecycle_event(db, provider, account, event)
        if not revoked and account.configuration.get("resource_ids"):
            refs = await adapter(provider, config, account).handle_event(event)
            await enqueue(db, account, "reconcile" if account.meta.get("reconcile_requested") else "incremental")
            if refs:
                pending = records(account.meta.get("pending_events", []))
                existing_ids = {item.get("id") for item in pending}
                pending.extend(ref.model_dump(mode="json") for ref in refs if ref.id not in existing_ids)
                if len(pending) > 5000:
                    raise ConnectorError("rate_limited", "Event queue is full; delivery can retry", 60)
                account.meta = {**account.meta, "pending_events": pending}
            # Deletions and old edits also receive a full reconciliation during the scheduled interval.
        audit(db, account.owner_id, "event_received", account.id, provider=provider)
    await db.commit()
    return {"accepted": True, "duplicate": False}
