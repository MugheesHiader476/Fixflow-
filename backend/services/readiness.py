"""Read-only health checks for the database and future AI integration."""

from functools import lru_cache

from alembic.config import Config
from alembic.script import ScriptDirectory
from asyncpg import PostgresError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from backend.config import ROOT, get_settings
from backend.db.models import Base, Document, DocumentChunk, KnowledgeSource
from backend.db.session import get_engine


@lru_cache
def expected_revision() -> str | None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/db/migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def database_readiness(owner: str | None = None) -> dict[str, object]:
    settings = get_settings()
    result: dict[str, object] = {
        "status": "degraded",
        "service": "fixflow-api",
        "api": "ok",
        "database": "unavailable",
        "pgvector": "unavailable",
        "schema": "unavailable",
        "revision": None,
        "expected_revision": expected_revision(),
        "sources": None,
        "documents": None,
        "chunks": None,
        "embedded_chunks": None,
        "pending_sources": None,
        "failed_sources": None,
        "embedding_configured": bool(
            settings.embedding_api_url and settings.embedding_model and settings.embedding_dim
        ),
        "ai_generation": "not_configured",
        "authentication_configured": bool(settings.fixflow_api_token),
    }
    try:
        async with get_engine().connect() as connection:
            # Never mutate application data during a health/readiness probe.
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result["database"] = "connected"
            enabled = await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')"))
            result["pgvector"] = "available" if enabled else "unavailable"
            tables = set(
                await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"))
            )
            if "alembic_version" in tables:
                result["revision"] = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if set(Base.metadata.tables).issubset(tables) and result["revision"] == expected_revision():
                result["schema"] = "ready"
                if owner is None:
                    if enabled:
                        result["status"] = "ok"
                    return result
                for name, model in (("sources", KnowledgeSource), ("documents", Document), ("chunks", DocumentChunk)):
                    query = select(func.count()).select_from(model)
                    if model is KnowledgeSource:
                        query = query.where(KnowledgeSource.owner_id == owner)
                    else:
                        source_column = Document.source_id if model is Document else DocumentChunk.source_id
                        query = query.join(KnowledgeSource, source_column == KnowledgeSource.id).where(
                            KnowledgeSource.owner_id == owner
                        )
                    result[name] = await connection.scalar(query)
                result["embedded_chunks"] = await connection.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .join(KnowledgeSource)
                    .where(DocumentChunk.embedding.is_not(None), KnowledgeSource.owner_id == owner)
                )
                for label, statuses in (
                    ("pending_sources", ("uploaded", "processing", "chunked")),
                    ("failed_sources", ("failed",)),
                ):
                    result[label] = await connection.scalar(
                        select(func.count())
                        .select_from(KnowledgeSource)
                        .where(KnowledgeSource.status.in_(statuses), KnowledgeSource.owner_id == owner)
                    )
            else:
                result["schema"] = "migration_required"
            if enabled and result["schema"] == "ready":
                result["status"] = "ok"
    except (SQLAlchemyError, PostgresError, OSError, ValueError, TimeoutError):
        # A reachable database can still have missing tables or an incompatible schema.
        result["status"] = "degraded"
    return result
