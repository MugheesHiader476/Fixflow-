"""Explicit source indexing and evidence answers, authenticated through the same gateway."""

import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.rates import reserve
from backend.db.models import KnowledgeSource, PassageIndex
from backend.db.session import get_session
from backend.repositories.evidence import write_index
from backend.repositories.parsed import parsed_source
from backend.schemas.evidence import EmbeddingReceipt, EvidenceAnswer, EvidenceRequest, EvidenceResult, IndexRequest
from backend.security.policy import PolicyViolation
from backend.security.runtime import request_policy
from backend.services import embeddings, evidence
from backend.services.access import owner_id

router = APIRouter(prefix="/evidence")
Database = Annotated[AsyncSession, Depends(get_session)]


@router.post("/sources/{source_id}/index")
async def index(source_id: UUID, payload: IndexRequest, db: Database) -> dict[str, object]:
    key = int.from_bytes(source_id.bytes[:8], "big", signed=True)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise HTTPException(409, "Source is currently processing")
    result = await parsed_source(db, source_id, owner_id(db))
    if result is None:
        raise HTTPException(404, "Accessible parsed source not found")
    source = await db.get(KnowledgeSource, source_id)
    if source is None:
        raise HTTPException(404, "Accessible parsed source not found")
    try:
        count = await write_index(db, source, result, payload)
    except ValueError as error:
        raise HTTPException(
            422, "Passage indexing failed within structural or processing limits; needs review"
        ) from error
    await db.commit()
    return {"source_id": str(source_id), "project": payload.project, "passages": count}


@router.post("/search", response_model=EvidenceResult)
async def search(payload: EvidenceRequest, db: Database) -> EvidenceResult:
    try:
        request_policy(payload.question)
    except PolicyViolation as error:
        raise HTTPException(403, str(error)) from error
    try:
        return await evidence.search(db, payload, get_settings().gemini)
    except ValueError as error:
        raise HTTPException(409, "Indexed evidence could not be verified; refresh or re-index the source") from error


@router.post("/sources/{source_id}/remove-index")
async def remove_index(source_id: UUID, db: Database) -> dict[str, bool]:
    key = int.from_bytes(source_id.bytes[:8], "big", signed=True)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise HTTPException(409, "Source is currently processing")
    source = await db.scalar(
        select(KnowledgeSource.id).where(KnowledgeSource.id == source_id, KnowledgeSource.owner_id == owner_id(db))
    )
    if source is None:
        raise HTTPException(404, "Source not found")
    await db.execute(delete(PassageIndex).where(PassageIndex.source_id == source_id))
    await db.commit()
    return {"removed": True}


@router.post("/sources/{source_id}/embed", response_model=EmbeddingReceipt, status_code=202)
async def prepare_embeddings(source_id: UUID, db: Database) -> EmbeddingReceipt:
    try:
        return await embeddings.prepare(db, source_id, get_settings().gemini)
    except ValueError as error:
        raise HTTPException(409, "Indexed evidence could not be verified; refresh or re-index the source") from error


@router.get("/sources/{source_id}/embedding-status", response_model=EmbeddingReceipt)
async def embedding_status(source_id: UUID, db: Database) -> EmbeddingReceipt:
    try:
        return await embeddings.receipt(db, source_id, get_settings().gemini)
    except ValueError as error:
        raise HTTPException(409, "Indexed evidence could not be verified; refresh or re-index the source") from error


@router.post("/answer", response_model=EvidenceAnswer)
async def answer(payload: EvidenceRequest, db: Database) -> EvidenceAnswer:
    try:
        request_policy(payload.question)
    except PolicyViolation as error:
        raise HTTPException(403, str(error)) from error
    started = time.perf_counter()
    config = get_settings().evidence
    if payload.synthesize:
        await reserve("evidence-answer:" + owner_id(db), 60, config.requests_per_minute)
    try:
        result = await evidence.answer(db, payload, config, get_settings().gemini)
    except ValueError as error:
        raise HTTPException(409, "Indexed evidence could not be verified; refresh or re-index the source") from error
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return result
