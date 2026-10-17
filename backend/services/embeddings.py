"""Durable bounded embedding jobs and one-call hybrid search; no implicit paid backfill."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from asyncpg import PostgresError
from fastapi import HTTPException
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError
from backend.connectors.rates import reserve
from backend.db.models import (
    EmbeddingJob,
    EvidenceEmbedding,
    EvidencePassage,
    KnowledgeSource,
    PassageEmbedding,
    PassageIndex,
)
from backend.db.session import get_session_factory
from backend.processing.embedding_input import (
    DIMENSIONS,
    INPUT_FORMAT_VERSION,
    MODEL,
    document_input,
    input_hash,
    profile,
)
from backend.repositories import embeddings as repository
from backend.repositories.evidence import authorized, reauthorize, verified
from backend.repositories.parsed import parsed_source
from backend.schemas.evidence import EmbeddingReceipt, EvidenceHit, EvidenceRequest, GeminiConfig
from backend.services.access import owner_id
from backend.services.gemini_embeddings import EmbeddingUsage, embed, normalized

logger = logging.getLogger(__name__)


async def source_lock(db: AsyncSession, source_id: UUID) -> None:
    key = int.from_bytes(source_id.bytes[:8], "big", signed=True)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise HTTPException(409, "Source is currently processing")


async def receipt(db: AsyncSession, source_id: UUID, config: GeminiConfig) -> EmbeddingReceipt:
    if await parsed_source(db, source_id, owner_id(db)) is None:
        raise HTTPException(404, "Accessible parsed source not found")
    index = await db.get(PassageIndex, source_id, populate_existing=True)
    if index is None:
        raise HTTPException(409, "Add the parsed source to a project first")
    request = EvidenceRequest(project=index.project, question="Preparation status", source_ids=[source_id])
    total_query = authorized(db, request).with_only_columns(func.count()).correlate(None).scalar_subquery()
    prepared_query = (
        authorized(db, request)
        .where(repository.cached(config).exists())
        .with_only_columns(func.count())
        .correlate(None)
        .scalar_subquery()
    )
    snapshot = (
        await db.execute(
            select(PassageIndex, EmbeddingJob, total_query, prepared_query)
            .outerjoin(EmbeddingJob, EmbeddingJob.source_id == PassageIndex.source_id)
            .where(PassageIndex.source_id == source_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if snapshot is None:
        raise HTTPException(409, "Source index changed; refresh preparation status")
    index, job, total, prepared = snapshot
    return EmbeddingReceipt.model_validate(
        {
            "source_id": str(source_id),
            "prepared": prepared,
            "remaining": total - prepared,
            "embedding_calls": job.embedding_calls if job else 0,
            "input_tokens": job.input_tokens if job else None,
            "status": job.status if job else "not_queued",
            "active": index.vector_fingerprint == index.fingerprint
            and index.vector_profile == profile(config.revision)
            and prepared == total,
            "error": job.error if job else None,
        }
    )


async def prepare(db: AsyncSession, source_id: UUID, config: GeminiConfig) -> EmbeddingReceipt:
    await source_lock(db, source_id)
    result = await receipt(db, source_id, config)
    if not config.configured:
        raise HTTPException(503, "Semantic search requires explicit GEMINI__MODEL and GEMINI__API_KEY configuration")
    index = await db.get(PassageIndex, source_id)
    if index is None:
        raise ValueError("The source passage index is unavailable")
    job = await db.get(EmbeddingJob, source_id)
    identity = profile(config.revision)
    if job is None:
        db.add(EmbeddingJob(source_id=source_id, source_version=index.fingerprint, profile=identity))
    elif job.source_version != index.fingerprint or job.profile != identity or job.status in {"failed", "cancelled"}:
        job.source_version, job.profile = index.fingerprint, identity
        job.status, job.run_token, job.failures, job.error = "queued", None, 0, None
        job.next_attempt_at = datetime.now(UTC)
    elif job.status == "complete" and not result.active:
        job.status, job.next_attempt_at = "queued", datetime.now(UTC)
    await db.commit()
    return await receipt(db, source_id, config)


async def activate(db: AsyncSession, job: EmbeddingJob, index: PassageIndex, config: GeminiConfig) -> None:
    """Publish all current memberships and the activation pointer in this transaction."""
    rows = select(EvidencePassage.id, EvidencePassage.source_id, EvidencePassage.embedding_input_hash).where(
        EvidencePassage.source_id == job.source_id, repository.cached(config).exists()
    )
    total = int(
        await db.scalar(
            select(func.count()).select_from(EvidencePassage).where(EvidencePassage.source_id == job.source_id)
        )
        or 0
    )
    ready = int(await db.scalar(select(func.count()).select_from(rows.subquery())) or 0)
    if total != ready:
        return
    await db.execute(delete(PassageEmbedding).where(PassageEmbedding.source_id == job.source_id))
    await db.execute(
        insert(PassageEmbedding).from_select(
            ["passage_id", "source_id", "input_hash", "profile", "source_version"],
            rows.add_columns(
                literal(job.profile, PassageEmbedding.profile.type),
                literal(job.source_version, PassageEmbedding.source_version.type),
            ),
        )
    )
    index.vector_fingerprint, index.vector_profile = job.source_version, job.profile
    job.status, job.run_token, job.error = "complete", None, None


async def process_batch(source_id: UUID, config: GeminiConfig | None = None) -> None:
    """Claim one batch with an expiring lease; release every connection/lock over HTTP."""
    config = config or get_settings().gemini
    now = datetime.now(UTC)
    token = uuid4()
    async with get_session_factory().begin() as db:
        await source_lock(db, source_id)
        job = await db.get(EmbeddingJob, source_id, populate_existing=True)
        if job is None or job.status not in {"queued", "running"} or job.next_attempt_at > now:
            return
        source = await db.get(KnowledgeSource, source_id)
        if source is None:
            return
        owner = source.owner_id
        db.info["owner_id"] = owner
        index = await db.get(PassageIndex, source_id)
        if (
            index is None
            or index.fingerprint != job.source_version
            or job.profile != profile(config.revision)
            or await parsed_source(db, source_id, owner) is None
        ):
            job.status, job.run_token, job.error = (
                "cancelled",
                None,
                "Source, access or embedding configuration changed",
            )
            return
        if not config.configured:
            job.status, job.error = "failed", "Embedding model and API key are not configured"
            return
        request = EvidenceRequest(project=index.project, question="Prepare semantic search", source_ids=[source_id])
        rows = await repository.pending(db, request, config)
        passages = await verified(db, rows)
        if len(passages) != len(rows):
            job.status, job.error = "cancelled", "Source access changed"
            return
        inputs = {input_hash(p.prefix, p.content): document_input(p.prefix, p.content) for p in passages}
        for row in rows:
            row.embedding_input_hash = input_hash(row.prefix, row.content)
        await db.flush()
        # Old nullable passage hashes may now match a previously validated cache.
        cache_rows = list(
            await db.scalars(
                select(EvidenceEmbedding).where(
                    EvidenceEmbedding.source_id == source_id,
                    EvidenceEmbedding.input_hash.in_(inputs),
                    EvidenceEmbedding.profile == job.profile,
                )
            )
        )
        for cached in cache_rows:
            if (
                cached.model != MODEL
                or cached.revision != config.revision
                or cached.dimensions != DIMENSIONS
                or cached.input_format_version != INPUT_FORMAT_VERSION
            ):
                job.status, job.error = (
                    "failed",
                    "Cached embedding configuration mismatch; remove and rebuild the index",
                )
                return
            normalized([float(value) for value in cached.embedding])
        existing = {cached.input_hash for cached in cache_rows}
        missing = {key: text for key, text in inputs.items() if key not in existing}
        if not missing:
            await activate(db, job, index, config)
            return
        version, identity = job.source_version, job.profile
        job.status, job.run_token = "running", token
        job.next_attempt_at = now + timedelta(seconds=config.timeout_seconds + 30)
    usage = EmbeddingUsage()
    vectors: list[list[float]] = []
    failure: str | None = None
    retry_delay = 20.0
    retryable = True
    try:
        await reserve("evidence-embed:" + owner, 60, config.requests_per_minute)
        await reserve("evidence-embed-source:" + str(source_id), 20)
        async with get_session_factory()() as db:
            db.info["owner_id"] = owner
            if not await reauthorize(db, request, [EvidenceHit(passage=p, score=0) for p in passages]):
                failure, retryable = "Source access changed before preparation", False
        if failure is None:
            vectors = await embed(list(missing.values()), config, usage)
            if len(vectors) != len(missing):
                raise ValueError("Embedding batch mapping mismatch")
            vectors = [normalized(vector) for vector in vectors]
    except ConnectorError as error:
        failure, retry_delay = "Embedding rate limit; preparation will resume", max(1, error.retry_after or 60)
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, OverflowError) as error:
        failure = "Embedding batch failed; lexical search remains available"
        retryable = not isinstance(error, (ValueError, TypeError, OverflowError))
    async with get_session_factory().begin() as db:
        await source_lock(db, source_id)
        db.info["owner_id"] = owner
        job = await db.get(EmbeddingJob, source_id, populate_existing=True)
        if job is None or job.run_token != token or job.source_version != version or job.profile != identity:
            return
        job.embedding_calls += usage.calls
        if usage.calls:
            job.input_tokens = (
                job.input_tokens + usage.input_tokens
                if job.input_tokens is not None and usage.input_tokens is not None
                else None
            )
        index = await db.get(PassageIndex, source_id, populate_existing=True)
        if (
            index is None
            or index.fingerprint != version
            or not await reauthorize(db, request, [EvidenceHit(passage=p, score=0) for p in passages])
        ):
            job.status, job.run_token, job.error = "cancelled", None, "Source or access changed; results discarded"
            return
        job.run_token = None
        if failure:
            if usage.calls:
                job.failures += 1
                retry_delay *= 2 ** (job.failures - 1)
            job.status = "queued" if retryable and job.failures < 3 else "failed"
            job.error, job.next_attempt_at = failure, datetime.now(UTC) + timedelta(seconds=retry_delay)
            return
        await db.execute(
            insert(EvidenceEmbedding)
            .values(
                [
                    {
                        "source_id": source_id,
                        "profile": identity,
                        "input_hash": key,
                        "embedding": vector,
                        "model": MODEL,
                        "revision": config.revision,
                        "dimensions": DIMENSIONS,
                        "input_format_version": INPUT_FORMAT_VERSION,
                    }
                    for key, vector in zip(missing, vectors, strict=True)
                ]
            )
            .on_conflict_do_nothing()
        )
        job.status, job.failures, job.error = "queued", 0, None
        job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=20)
        await activate(db, job, index, config)


async def embedding_worker() -> None:
    while True:
        try:
            async with get_session_factory()() as db:
                ids = list(
                    await db.scalars(
                        select(EmbeddingJob.source_id)
                        .where(
                            EmbeddingJob.status.in_(("queued", "running")),
                            EmbeddingJob.next_attempt_at <= func.clock_timestamp(),
                        )
                        .order_by(EmbeddingJob.next_attempt_at)
                        .limit(2)
                    )
                )
            for source_id in ids:
                try:
                    await process_batch(source_id)
                except HTTPException as error:
                    if error.status_code != 409:
                        raise
        except (SQLAlchemyError, PostgresError, OSError, ValueError, TimeoutError) as error:
            logger.error("Embedding queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(2)


def fuse(lexical: list[EvidenceHit], semantic: list[EvidenceHit]) -> list[EvidenceHit]:
    scores: dict[str, float] = {}
    hits: dict[str, EvidenceHit] = {}
    for ranked in [sorted(lexical, key=lambda hit: (-hit.score, hit.passage.id)), semantic]:
        seen: set[str] = set()
        for position, hit in enumerate(ranked):
            key = hit.passage.id
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (61 + position)
            previous = hits.get(key)
            hits[key] = hit.model_copy(update={"lexical_rank": previous.lexical_rank}) if previous else hit
    return sorted(
        [hits[key].model_copy(update={"score": score}) for key, score in scores.items()],
        key=lambda hit: (-hit.score, hit.passage.id),
    )


async def hybrid(
    db: AsyncSession,
    request: EvidenceRequest,
    lexical: list[EvidenceHit],
    config: GeminiConfig,
    usage: EmbeddingUsage,
    warnings: list[str],
) -> list[EvidenceHit]:
    total, ready = await repository.counts(db, request, config)
    if not config.configured or not ready:
        warnings.append(
            "Hybrid search unavailable: configure Gemini model/key and complete source preparation. "
            "Lexical search used."
        )
        return lexical
    if ready < total:
        warnings.append(f"Semantic coverage is partial: {ready} of {total} accessible project passages are prepared.")
    await db.rollback()
    try:
        await reserve("evidence-embed:" + owner_id(db), 60, config.requests_per_minute)
        vectors = await embed(["task: search result | query: " + request.question], config, usage)
        if len(vectors) != 1:
            raise ValueError("Query embedding count mismatch")
        query_vector = normalized(vectors[0])
    except (ConnectorError, httpx.HTTPError, TimeoutError, ValueError, TypeError, OverflowError):
        usage.completed = False
        warnings.append("The single query embedding attempt failed or was rate limited; lexical search used.")
        return lexical
    return fuse(lexical, await repository.candidates(db, request, query_vector, config))
