"""Disposable database fixtures and SQL proofs for the real application browser harness."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from backend.config import get_settings
from backend.connectors.vault import TokenVault
from backend.db.models import ConnectorAccount, Document, DocumentChunk, IngestionArtifact
from backend.db.session import close_database, get_engine, get_session_factory
from backend.repositories.parsed import parsed_source
from backend.schemas.connectors import Credentials

OWNER_A = "user_preembedding_a"
OWNER_B = "user_preembedding_b"


def configure() -> None:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url or not (make_url(url).database or "").endswith("_test"):
        raise ValueError("An explicit disposable TEST_DATABASE_URL ending in _test is required")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()


async def reset() -> None:
    async with get_engine().begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE knowledge_sources, documents, document_chunks, debug_sessions, chat_messages, "
                "saved_solutions, "
                "connector_accounts, connector_credentials, connector_oauth_states, connector_sync_jobs, "
                "connector_resources, "
                "connector_subscriptions, connector_events, connector_audit, connector_rate_limits CASCADE"
            )
        )
    from backend.db.models import DebugSession  # noqa: PLC0415
    from backend.tests.history import seed_history  # noqa: PLC0415

    history, _ = await seed_history()
    async with get_session_factory().begin() as db:
        row = await db.get(DebugSession, UUID(str(history["sessionId"])))
        assert row is not None
        row.owner_id = OWNER_A
        vault = TokenVault(get_settings().connectors)
        credentials = Credentials(
            access_token=SecretStr(os.environ["AUDIT_PROVIDER_TOKEN"]), refresh_token=SecretStr("test-refresh-only")
        )
        reference = await vault.store(db, OWNER_A, credentials)
        db.add(
            ConnectorAccount(
                id=uuid4(),
                owner_id=OWNER_A,
                provider="gmail",
                external_account_id="synthetic-account",
                display_name="audit@example.invalid",
                credential_reference=reference,
                status="connected",
                authentication_status="valid",
                provider_health="available",
                sync_status="idle",
                configuration={"resource_ids": []},
                reconcile_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    print(json.dumps({"history_id": history["sessionId"]}))


async def proof(identifier: str) -> None:
    async with get_session_factory()() as db:
        source_id = UUID(identifier)
        own = await parsed_source(db, source_id, OWNER_A)
        foreign = await parsed_source(db, source_id, OWNER_B)
        documents = await db.scalar(select(func.count()).select_from(Document).where(Document.source_id == source_id))
        chunks = await db.scalar(select(func.count()).select_from(DocumentChunk))
        artifacts = await db.scalar(
            select(func.count()).select_from(IngestionArtifact).where(IngestionArtifact.source_id == source_id)
        )
        print(
            json.dumps(
                {
                    "source_id": identifier,
                    "owner_authorized": own is not None,
                    "foreign_excluded": foreign is None,
                    "canonical_blocks": len(own.canonical.blocks) if own else 0,
                    "documents": documents,
                    "artifacts": artifacts,
                    "chunks": chunks,
                    "engine": own.engine_version if own else None,
                }
            )
        )


async def main() -> None:
    configure()
    try:
        if sys.argv[1] == "reset":
            await reset()
        elif sys.argv[1] == "proof":
            await proof(sys.argv[2])
        else:
            raise ValueError("Unknown evaluation operation")
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
