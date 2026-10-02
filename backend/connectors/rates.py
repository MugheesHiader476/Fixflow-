"""Database rate reservations coordinate API replicas and survive restarts."""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.connectors.core import ConnectorError
from backend.db.models import ConnectorRateLimit
from backend.db.session import get_session_factory


async def reserve(key: str, interval: float, limit: int = 1) -> None:
    identifier = hashlib.sha256(key.encode()).hexdigest()
    now = datetime.now(UTC)
    async with get_session_factory().begin() as db:
        await db.execute(
            insert(ConnectorRateLimit)
            .values(key=identifier, next_allowed_at=now, count=0)
            .on_conflict_do_nothing(index_elements=[ConnectorRateLimit.key])
        )
        row = await db.scalar(select(ConnectorRateLimit).where(ConnectorRateLimit.key == identifier).with_for_update())
        if row is None:
            raise ConnectorError("rate_limited", "Request reservation is unavailable; retry shortly", 60)
        if row.next_allowed_at <= now:
            row.next_allowed_at, row.count = now + timedelta(seconds=interval), 0
        if row.count >= limit:
            raise ConnectorError(
                "rate_limited",
                "Request limit reached; retry after the displayed delay",
                (row.next_allowed_at - now).total_seconds(),
            )
        row.count += 1
