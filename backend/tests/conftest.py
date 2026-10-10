from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.config import get_settings
from backend.db.session import close_database, get_engine, get_session_factory
from backend.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def migrated_database() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database ending in _test")
    if not (make_url(url).database or "").endswith("_test"):
        raise ValueError("Refusing to reset a database whose name does not end in _test")
    asyncio.run(clear_test_sources(url))
    env = {**os.environ, "DATABASE_URL": url}
    for command in (["downgrade", "base"], ["upgrade", "head"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *command], env=env, check=True, timeout=60)
    return url


async def clear_test_sources(url: str) -> None:
    # Only the disposable database validated above is reset. This permits downgrade
    # when a prior test created distinct provider identities with identical content.
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            if await connection.scalar(text("SELECT to_regclass('public.knowledge_sources') IS NOT NULL")):
                await connection.execute(text("TRUNCATE knowledge_sources CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
async def database(
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    monkeypatch.setenv("FIXFLOW_API_TOKEN", "test-only-gateway-token-at-least-32-characters")
    monkeypatch.setenv("EMBEDDING_API_URL", "")
    monkeypatch.setenv("OLLAMA_URL", "")
    monkeypatch.setenv("RETRIEVAL_MODE", "keyword")
    monkeypatch.setenv("EMBEDDING_AUTO_PROCESS", "false")
    monkeypatch.setenv("EMBEDDING_MODEL_DIGEST", "")
    monkeypatch.setenv("EMBEDDING_PROFILE", "plain-v1")
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("FIXFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_DIM", "")
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    monkeypatch.setenv("GENERATION_OLLAMA_URL", "")
    monkeypatch.setenv("GENERATION_MODEL", "")
    monkeypatch.setenv("GENERATION_MODEL_DIGEST", "")
    get_settings.cache_clear()
    await close_database()
    async with get_engine().begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE knowledge_sources, documents, document_chunks, "
                "debug_sessions, chat_messages, saved_solutions"
                ", connector_accounts, connector_credentials, connector_oauth_states, connector_sync_jobs, "
                "connector_resources, connector_subscriptions, connector_events, connector_audit, "
                "connector_rate_limits CASCADE"
            )
        )
    try:
        yield
    finally:
        await close_database()
        get_settings.cache_clear()


@pytest.fixture
async def db(database: None) -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


@pytest.fixture
async def client(database: None) -> AsyncIterator[httpx.AsyncClient]:
    # Worker execution is explicit in tests; separate lifecycle coverage exercises startup recovery.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer test-only-gateway-token-at-least-32-characters",
            "X-FixFlow-User-Id": "user_test",
        },
    ) as client:
        yield client
