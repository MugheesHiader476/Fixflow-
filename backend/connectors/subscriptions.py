"""Optional Gmail/Drive notification channels; polling remains the durable fallback."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select

from backend.connectors.core import Connector, ConnectorError, number, record, string
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.gmail import GmailConnector
from backend.db.models import ConnectorAccount, ConnectorSubscription
from backend.db.session import get_session_factory


async def ensure_subscription(connector: Connector, account: ConnectorAccount) -> None:
    if not isinstance(connector, (GmailConnector, GoogleDriveConnector)) or not connector.config.events_enabled:
        return
    async with get_session_factory()() as db:
        current = await db.scalar(
            select(ConnectorSubscription)
            .where(
                ConnectorSubscription.account_id == account.id,
                ConnectorSubscription.expires_at > datetime.now(UTC) + timedelta(hours=2),
            )
            .limit(1)
        )
        if current:
            return
    identifier, secret = uuid4(), secrets.token_urlsafe(32)
    if isinstance(connector, GmailConnector):
        topic = connector.config.gmail_pubsub_topic
        if not topic or not connector.config.gmail_pubsub_service_account:
            raise ConnectorError(
                "not_configured", "Gmail notifications require a Pub/Sub topic and authenticated push identity"
            )
        result = record(
            await connector.http.request(
                "POST",
                connector.api_root + "/watch",
                token=await connector.access_token(),
                payload={"topicName": topic},
            )
        )
        expires = datetime.fromtimestamp(number(result["expiration"]) / 1000, UTC)
        resource = account.external_account_id
    else:
        result = await connector.get("/changes/startPageToken", {"supportsAllDrives": "true"})
        result = record(
            await connector.http.request(
                "POST",
                connector.api_root + "/changes/watch",
                token=await connector.access_token(),
                params={
                    "pageToken": string(result["startPageToken"]),
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                },
                payload={
                    "id": str(identifier),
                    "type": "web_hook",
                    "address": connector.config.public_url + "/api/connectors/events/google_drive",
                    "token": secret,
                    "expiration": str(int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000)),
                },
            )
        )
        expires, resource = (
            datetime.fromtimestamp(number(result["expiration"]) / 1000, UTC),
            string(result["resourceId"]),
        )
    async with get_session_factory().begin() as db:
        live = await db.get(ConnectorAccount, account.id, with_for_update=True)
        if live is None or live.status not in {"connected", "syncing", "connected_with_warning"}:
            return
        db.add(
            ConnectorSubscription(
                id=identifier,
                account_id=account.id,
                resource_id=resource,
                token_hash=hashlib.sha256(secret.encode()).hexdigest(),
                expires_at=expires,
            )
        )
        # Expired rows are no longer trusted by event verification.
        await db.execute(
            delete(ConnectorSubscription).where(
                ConnectorSubscription.account_id == account.id, ConnectorSubscription.expires_at < datetime.now(UTC)
            )
        )


async def stop_subscriptions(connector: Connector, account: ConnectorAccount, token: str | None = None) -> None:
    if isinstance(connector, GmailConnector):
        await connector.http.request(
            "POST", connector.api_root + "/stop", token=token or await connector.access_token(), raw=True
        )
    elif isinstance(connector, GoogleDriveConnector):
        async with get_session_factory()() as db:
            subscriptions = list(
                await db.scalars(select(ConnectorSubscription).where(ConnectorSubscription.account_id == account.id))
            )
        for subscription in subscriptions:
            await connector.http.request(
                "POST",
                connector.api_root + "/channels/stop",
                token=token or await connector.access_token(),
                payload={"id": str(subscription.id), "resourceId": subscription.resource_id},
                raw=True,
            )
