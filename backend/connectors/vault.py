"""Encrypted application storage; only opaque credential references leave this service."""

import json
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError
from backend.db.models import ConnectorCredential
from backend.schemas.connectors import Credentials


class TokenVault:
    def __init__(self, config: ConnectorConfig) -> None:
        if config.vault_key is None:
            raise ConnectorError("not_configured", "Connector credential encryption is not configured")
        try:
            self.cipher = MultiFernet(
                [Fernet(key.get_secret_value()) for key in [config.vault_key, *config.vault_previous_keys]]
            )
        except ValueError as error:
            raise ConnectorError("not_configured", "Connector encryption key is invalid") from error

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeError) as error:
            raise ConnectorError("credential_unavailable", "Stored connector credentials are unavailable") from error

    def encode(self, credentials: Credentials) -> str:
        payload = credentials.model_dump(mode="json", exclude={"access_token", "refresh_token"})
        payload.update(
            access_token=credentials.access_token.get_secret_value(),
            refresh_token=credentials.refresh_token.get_secret_value() if credentials.refresh_token else None,
        )
        return self.encrypt(json.dumps(payload))

    async def store(self, db: AsyncSession, owner: str, credentials: Credentials) -> UUID:
        row = ConnectorCredential(owner_id=owner, ciphertext=self.encode(credentials))
        db.add(row)
        await db.flush()
        return row.id

    async def retrieve(self, db: AsyncSession, owner: str, reference: UUID, *, lock: bool = False) -> Credentials:
        statement = select(ConnectorCredential).where(
            ConnectorCredential.id == reference,
            ConnectorCredential.owner_id == owner,
            ConnectorCredential.revoked_at.is_(None),
        )
        row = await db.scalar(statement.with_for_update() if lock else statement)
        if row is None:
            raise ConnectorError("reauth_required", "Reconnect this account to continue")
        return Credentials.model_validate_json(self.decrypt(row.ciphertext))

    async def rotate(self, db: AsyncSession, owner: str, reference: UUID, credentials: Credentials) -> None:
        row = await db.scalar(
            select(ConnectorCredential)
            .where(
                ConnectorCredential.id == reference,
                ConnectorCredential.owner_id == owner,
                ConnectorCredential.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if row is None:
            raise ConnectorError("reauth_required", "Reconnect this account to continue")
        row.ciphertext = self.encode(credentials)

    async def revoke(self, db: AsyncSession, owner: str, reference: UUID) -> None:
        row = await db.scalar(
            select(ConnectorCredential)
            .where(ConnectorCredential.id == reference, ConnectorCredential.owner_id == owner)
            .with_for_update()
        )
        if row:
            row.ciphertext, row.revoked_at = "", datetime.now(UTC)
