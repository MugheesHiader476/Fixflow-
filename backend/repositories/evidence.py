"""PostgreSQL passage writes/reads with authorization before private payload decoding."""

import asyncio
import logging
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.config import get_settings
from backend.db.models import EmbeddingJob, EvidenceEmbedding, EvidencePassage, KnowledgeSource, PassageIndex
from backend.db.session import get_session_factory
from backend.processing.embedding_input import input_hash, profile
from backend.processing.passages import PASSAGE_VERSION, build_passages, source_metadata, validate_passage
from backend.processing.pipeline.validation import validate_result
from backend.repositories.parsed import parsed_source
from backend.repositories.source_access import accessible_source
from backend.schemas.evidence import EvidenceHit, EvidenceRequest, IndexRequest, Passage
from backend.schemas.pipeline import PipelineResult, digest
from backend.services.access import owner_id

logger = logging.getLogger(__name__)


def index_fingerprint(result: PipelineResult, labels: IndexRequest) -> str:
    return digest(
        [
            PASSAGE_VERSION,
            result.canonical.content_hash,
            result.canonical.source.sha256,
            labels.model_dump(mode="json", exclude={"force"}),
            source_metadata(result.canonical, labels),
        ]
    )


async def write_index(db: AsyncSession, source: KnowledgeSource, result: PipelineResult, labels: IndexRequest) -> int:
    validate_result(result)
    permissions = result.canonical.source.context.get("permissions")
    if (
        result.canonical.source.source_id != str(source.id)
        or result.canonical.source.sha256 != source.file_hash
        or not isinstance(permissions, dict)
        or permissions.get("application_owner") != source.owner_id
    ):
        raise ValueError("Index source registration mismatch")
    specification = labels.model_dump(mode="json", exclude={"force"})
    fingerprint = index_fingerprint(result, labels)
    index = await db.get(PassageIndex, source.id)
    if index is not None and index.fingerprint == fingerprint and not labels.force:
        return int(
            await db.scalar(
                select(func.count()).select_from(EvidencePassage).where(EvidencePassage.source_id == source.id)
            )
            or 0
        )
    passages = await asyncio.to_thread(build_passages, result.canonical, labels)
    if index is None:
        index = PassageIndex(
            source_id=source.id, project=labels.project, specification=specification, fingerprint=fingerprint
        )
        db.add(index)
    else:
        index.project, index.specification, index.fingerprint = labels.project, specification, fingerprint
        index.vector_fingerprint, index.vector_profile = None, None
        job = await db.get(EmbeddingJob, source.id)
        if job is not None:
            job.status, job.run_token, job.error = "cancelled", None, "Source changed; queue semantic preparation again"
    await db.flush()
    await db.execute(delete(EvidencePassage).where(EvidencePassage.source_id == source.id))
    db.add_all(
        [
            EvidencePassage(
                id=p.id,
                source_id=source.id,
                ordinal=p.ordinal,
                source_hash=p.source_hash,
                content=p.content,
                prefix=p.prefix,
                payload=p.model_dump(mode="json"),
                embedding_input_hash=input_hash(p.prefix, p.content),
            )
            for p in passages
        ]
    )
    await db.flush()
    # Preserve only exact unchanged inputs within this source. Replaced content,
    # project/date changes and removed passages cannot retain stale cache entries.
    await db.execute(
        delete(EvidenceEmbedding).where(
            EvidenceEmbedding.source_id == source.id,
            EvidenceEmbedding.input_hash.not_in(
                select(EvidencePassage.embedding_input_hash).where(EvidencePassage.source_id == source.id)
            ),
        )
    )
    return len(passages)


AUTOMATIC_PROJECT = "workspace"


def automatic_labels() -> IndexRequest:
    return IndexRequest(project=AUTOMATIC_PROJECT)


async def refresh_index(db: AsyncSession, source: KnowledgeSource, result: PipelineResult) -> None:
    """Keep every parsed source searchable as structure-aware passages.

    Owner-labelled indexes keep their labels and fail loudly. Automatic indexes never fail
    parsing: an unindexable source is left to whole-document search instead.
    """
    index = await db.get(PassageIndex, source.id)
    labels = IndexRequest.model_validate(index.specification) if index is not None else automatic_labels()
    automatic = labels == automatic_labels()
    if not automatic:
        await write_index(db, source, result, labels)
    else:
        try:
            async with db.begin_nested():
                await write_index(db, source, result, labels)
        except ValueError:
            logger.warning("Automatic passage indexing skipped source=%s; whole-document search remains", source.id)
            # A stale index would fail verification for every search, so remove it.
            await db.execute(delete(PassageIndex).where(PassageIndex.source_id == source.id))
            return
    embedding = get_settings().embeddings
    if embedding.configured and embedding.auto_embed:
        await queue_embedding(db, source.id, profile(embedding.revision))


async def refresh_stale_indexes(batch: int = 50) -> int:
    """Rebuild passage indexes created by an older passage version or chunk setting; returns the count."""
    rebuilt, after = 0, None
    while True:
        async with get_session_factory()() as db:
            statement = select(PassageIndex.source_id).order_by(PassageIndex.source_id).limit(batch)
            if after is not None:
                statement = statement.where(PassageIndex.source_id > after)
            identifiers = list(await db.scalars(statement))
        if not identifiers:
            return rebuilt
        after = identifiers[-1]
        for identifier in identifiers:
            async with get_session_factory().begin() as db:
                key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
                if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
                    continue  # Being parsed right now; that persist refreshes the index itself.
                source = await db.get(KnowledgeSource, identifier)
                index = await db.get(PassageIndex, identifier)
                if source is None or index is None:
                    continue
                db.info["owner_id"] = source.owner_id
                result = await parsed_source(db, identifier, source.owner_id)
                if result is None:
                    continue
                labels = IndexRequest.model_validate(index.specification)
                if index.fingerprint == index_fingerprint(result, labels):
                    continue
                await refresh_index(db, source, result)
                rebuilt += 1


async def queue_embedding(db: AsyncSession, source_id: UUID, identity: str) -> None:
    """Queue the bounded, rate-limited embedding worker for the current index version only."""
    index = await db.get(PassageIndex, source_id)
    if index is None or (index.vector_fingerprint == index.fingerprint and index.vector_profile == identity):
        return
    job = await db.get(EmbeddingJob, source_id)
    if job is None:
        db.add(EmbeddingJob(source_id=source_id, source_version=index.fingerprint, profile=identity))
    elif job.source_version != index.fingerprint or job.profile != identity or job.status == "cancelled":
        job.source_version, job.profile = index.fingerprint, identity
        job.status, job.run_token, job.failures, job.error = "queued", None, 0, None
        job.next_attempt_at = datetime.now(UTC)


def authorized(db: AsyncSession, request: EvidenceRequest) -> Select[tuple[EvidencePassage]]:
    statement = (
        select(EvidencePassage)
        .join(KnowledgeSource, KnowledgeSource.id == EvidencePassage.source_id)
        .join(PassageIndex, PassageIndex.source_id == EvidencePassage.source_id)
        .where(
            KnowledgeSource.owner_id == owner_id(db),
            KnowledgeSource.status == "parsed",
            accessible_source(current_time=True),
            EvidencePassage.source_hash == KnowledgeSource.file_hash,
        )
        .execution_options(populate_existing=True)
    )
    if not request.all_projects:
        statement = statement.where(PassageIndex.project == request.project)
    if request.connector_account_ids is not None:
        selected = or_(KnowledgeSource.connector_account_id.in_(request.connector_account_ids))
        if request.include_manual_sources:
            selected = or_(KnowledgeSource.connector_account_id.is_(None), selected)
        statement = statement.where(selected)
    if request.source_ids:
        statement = statement.where(KnowledgeSource.id.in_(request.source_ids))
    return statement


async def verified(db: AsyncSession, rows: list[EvidencePassage]) -> list[Passage]:
    groups: dict[UUID, list[EvidencePassage]] = {}
    for row in rows:
        groups.setdefault(row.source_id, []).append(row)
    passages: list[Passage] = []
    for source_id, group in groups.items():
        result = await parsed_source(db, source_id, owner_id(db))
        if result is None:
            continue
        index = await db.get(PassageIndex, source_id, populate_existing=True)
        if index is None:
            continue
        labels = IndexRequest.model_validate(index.specification)
        if index.fingerprint != index_fingerprint(result, labels):
            # Built by another chunking/passage version: never serve it, but do not fail every search.
            # refresh_stale_indexes() rebuilds it at startup.
            logger.warning("Skipping stale passage index source=%s until it is rebuilt", source_id)
            continue
        metadata = source_metadata(result.canonical, labels)
        for row in group:
            passage = Passage.model_validate(row.payload)
            if (
                row.id != passage.id
                or row.content != passage.content
                or row.prefix != passage.prefix
                or row.ordinal != passage.ordinal
                or (
                    row.embedding_input_hash is not None
                    and row.embedding_input_hash != input_hash(passage.prefix, passage.content)
                )
                or any(passage.metadata.get(key) != value for key, value in metadata.items())
            ):
                raise ValueError("Passage projection mismatch")
            await asyncio.to_thread(validate_passage, result.canonical, passage)
            passages.append(passage)
    return passages


async def candidates(db: AsyncSession, request: EvidenceRequest) -> list[EvidenceHit]:
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+(?:_[^\W_]+)*", request.question.lower())))[:32]
    if not terms:
        return []
    # Bounded disjunction tolerates question words absent from the source; ranking
    # prefers coverage/proximity. No raw SQL or user-supplied tsquery syntax.
    query = func.websearch_to_tsquery("english", " OR ".join(terms))
    rank = func.ts_rank_cd(EvidencePassage.search_text, query, 32)
    body_match = func.to_tsvector("english", EvidencePassage.content).op("@@")(query)
    result = await db.execute(
        authorized(db, request)
        .add_columns(rank, body_match)
        .where(EvidencePassage.search_text.op("@@")(query))
        .order_by(rank.desc(), EvidencePassage.id)
        .limit(request.candidate_limit)
    )
    records = list(result)
    rows = [row[0] for row in records]
    ranks = {row[0].id: (float(row[1]), bool(row[2])) for row in records}
    passages = await verified(db, rows)
    hits: list[EvidenceHit] = []
    for p in passages:
        coverage = len(set(terms) & set(re.findall(r"\w+", p.content.lower()))) / len(terms)
        section_match = bool(set(terms) & set(re.findall(r"\w+", str(p.metadata.get("section", "")).lower())))
        rank_value, matched_body = ranks[p.id]
        # Keep stemming-only body matches; a shared Project prefix alone is not evidence.
        if matched_body or coverage or section_match:
            hits.append(EvidenceHit(passage=p, score=coverage + rank_value))
    hits.sort(key=lambda hit: (-hit.score, hit.passage.id))
    return [hit.model_copy(update={"lexical_rank": i + 1}) for i, hit in enumerate(hits)]


async def neighbours(db: AsyncSession, request: EvidenceRequest, hit: EvidenceHit) -> list[EvidenceHit]:
    p = hit.passage
    if p.kind == "slack":
        # Each message remains an independent source, permission lease and citation.
        source = await db.get(KnowledgeSource, UUID(p.source_id))
        if source is None or source.connector_account_id is None:
            return []
        statement = (
            authorized(db, request)
            .where(
                KnowledgeSource.connector_account_id == source.connector_account_id,
                EvidencePassage.payload["metadata"]["thread_id"].as_string() == str(p.metadata.get("thread_id", "")),
                EvidencePassage.payload["metadata"]["channel_id"].as_string() == str(p.metadata.get("channel_id", "")),
                EvidencePassage.id != p.id,
            )
            .order_by(EvidencePassage.payload["metadata"]["message_id"].as_string(), EvidencePassage.ordinal)
            .limit(6)
        )
    else:
        statement = (
            authorized(db, request)
            .where(
                EvidencePassage.source_id == UUID(p.source_id),
                EvidencePassage.ordinal.in_([p.ordinal - 1, p.ordinal + 1]),
                EvidencePassage.payload["group"].as_string() == p.group,
            )
            .order_by(EvidencePassage.ordinal)
        )
    values = await verified(db, list(await db.scalars(statement)))
    return [
        EvidenceHit(
            passage=value,
            score=0,
            reason=(
                "thread_root"
                if value.kind == "slack" and value.metadata.get("message_id") == value.metadata.get("thread_id")
                else "thread_reply"
                if value.kind == "slack"
                else "neighbour"
            ),
        )
        for value in values
    ]


async def reauthorize(db: AsyncSession, request: EvidenceRequest, hits: list[EvidenceHit]) -> bool:
    if not hits:
        return True
    # Fresh SQL/clock time after a model call; don't trust ORM identity-map state.
    rows = list(await db.scalars(authorized(db, request).where(EvidencePassage.id.in_([h.passage.id for h in hits]))))
    return {p.id for p in await verified(db, rows)} == {h.passage.id for h in hits}
