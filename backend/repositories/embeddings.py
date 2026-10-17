"""Exact cosine search through current owner/project/access and activated versions."""

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from backend.db.models import EvidenceEmbedding, EvidencePassage, PassageEmbedding, PassageIndex
from backend.processing.embedding_input import BATCH_SIZE, DIMENSIONS, INPUT_FORMAT_VERSION, MODEL, profile
from backend.repositories.evidence import authorized, verified
from backend.schemas.evidence import EvidenceHit, EvidenceRequest, GeminiConfig
from backend.services.gemini_embeddings import normalized


def compatible(config: GeminiConfig) -> tuple[ColumnElement[bool], ...]:
    return (
        EvidenceEmbedding.profile == profile(config.revision),
        EvidenceEmbedding.model == MODEL,
        EvidenceEmbedding.revision == config.revision,
        EvidenceEmbedding.dimensions == DIMENSIONS,
        EvidenceEmbedding.input_format_version == INPUT_FORMAT_VERSION,
    )


def cached(config: GeminiConfig) -> Select[tuple[EvidenceEmbedding]]:
    return select(EvidenceEmbedding).where(
        EvidenceEmbedding.source_id == EvidencePassage.source_id,
        EvidenceEmbedding.input_hash == EvidencePassage.embedding_input_hash,
        *compatible(config),
    )


def membership(config: GeminiConfig) -> tuple[ColumnElement[bool], ...]:
    return (
        PassageEmbedding.passage_id == EvidencePassage.id,
        PassageEmbedding.source_id == EvidencePassage.source_id,
        PassageEmbedding.input_hash == EvidencePassage.embedding_input_hash,
        PassageEmbedding.source_version == PassageIndex.fingerprint,
        PassageEmbedding.profile == profile(config.revision),
        PassageIndex.vector_fingerprint == PassageIndex.fingerprint,
        PassageIndex.vector_profile == profile(config.revision),
    )


async def counts(db: AsyncSession, request: EvidenceRequest, config: GeminiConfig) -> tuple[int, int]:
    total = int(await db.scalar(authorized(db, request).with_only_columns(func.count())) or 0)
    ready = int(
        await db.scalar(
            authorized(db, request)
            .where(exists(select(PassageEmbedding.passage_id).where(*membership(config))))
            .where(exists(cached(config)))
            .with_only_columns(func.count())
        )
        or 0
    )
    return total, ready


async def pending(db: AsyncSession, request: EvidenceRequest, config: GeminiConfig) -> list[EvidencePassage]:
    return list(
        await db.scalars(
            authorized(db, request).where(~exists(cached(config))).order_by(EvidencePassage.ordinal).limit(BATCH_SIZE)
        )
    )


async def candidates(
    db: AsyncSession, request: EvidenceRequest, vector: list[float], config: GeminiConfig
) -> list[EvidenceHit]:
    vector = normalized(vector)
    distance = EvidenceEmbedding.embedding.cosine_distance(vector)
    records = list(
        await db.execute(
            authorized(db, request)
            .join(PassageEmbedding, and_(*membership(config)))
            .join(
                EvidenceEmbedding,
                and_(
                    EvidenceEmbedding.source_id == PassageEmbedding.source_id,
                    EvidenceEmbedding.input_hash == PassageEmbedding.input_hash,
                    *compatible(config),
                ),
            )
            .add_columns(distance)
            .where(distance <= 1 - config.min_similarity)
            .order_by(distance, EvidencePassage.id)
            .limit(request.candidate_limit)
        )
    )
    ranks = {row[0].id: (i + 1, 1 - float(row[1])) for i, row in enumerate(records)}
    passages = await verified(db, [row[0] for row in records])
    return sorted(
        [
            EvidenceHit(
                passage=p,
                score=ranks[p.id][1],
                vector_rank=ranks[p.id][0],
                similarity=ranks[p.id][1],
                embedding_profile=profile(config.revision),
            )
            for p in passages
        ],
        key=lambda hit: (hit.vector_rank or 0, hit.passage.id),
    )
