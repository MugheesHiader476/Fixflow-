from __future__ import annotations

import hashlib
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import KnowledgeSource as SourceRecord
from backend.db.session import get_session
from backend.repositories.sources import list_sources
from backend.schemas.models import (
    ChatMessage,
    ChatRequest,
    DebugRequest,
    Diagnosis,
    KnowledgeSource,
    SavedSolution,
    SaveRequest,
    SessionSummary,
)
from backend.services import store
from backend.services.access import owner_id
from backend.services.diagnosis import DiagnosisProvider, diagnostic_text, get_diagnosis_provider
from backend.services.readiness import database_readiness
from backend.services.uploads import discard_upload, safe_filename, save_upload, validated_remote_url

router = APIRouter()
Database = Annotated[AsyncSession, Depends(get_session)]
Provider = Annotated[DiagnosisProvider, Depends(get_diagnosis_provider)]


@router.get("/readiness")
async def readiness(db: Database) -> JSONResponse:
    result = await database_readiness(owner_id(db))
    return JSONResponse(status_code=200 if result["status"] == "ok" else 503, content=result)


@router.post("/debug", response_model=Diagnosis)
async def debug(payload: DebugRequest, db: Database, provider: Provider) -> Diagnosis:
    if not diagnostic_text(payload):
        raise HTTPException(422, "Provide an error, code, or context")
    return await store.diagnose(db, payload, provider)


@router.post("/chat", response_model=ChatMessage)
async def chat(payload: ChatRequest, db: Database, provider: Provider) -> ChatMessage:
    try:
        session_id = UUID(payload.session_id)
    except ValueError as error:
        raise HTTPException(404, "Session not found") from error
    try:
        return await store.chat(db, session_id, payload.question, provider)
    except store.SessionNotFoundError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/sessions", response_model=list[SessionSummary])
async def sessions(db: Database) -> list[SessionSummary]:
    return await store.sessions(db)


@router.get("/sessions/{session_id}", response_model=Diagnosis)
async def session(session_id: UUID, db: Database) -> Diagnosis:
    result = await store.get_session(db, session_id)
    if result is None:
        raise HTTPException(404, "Session not found")
    return result


@router.get("/sources", response_model=list[KnowledgeSource])
async def sources(db: Database) -> list[KnowledgeSource]:
    return await list_sources(db)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def session_messages(session_id: UUID, db: Database) -> list[ChatMessage]:
    try:
        return await store.messages(db, session_id)
    except store.SessionNotFoundError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/sources/{source_id}", response_model=KnowledgeSource)
@router.get("/sources/{source_id}/status", response_model=KnowledgeSource)
async def source(source_id: UUID, db: Database) -> KnowledgeSource:
    result = await list_sources(db, source_id)
    if not result:
        raise HTTPException(404, "Source not found")
    return result[0]


@router.get("/saved", response_model=list[SavedSolution])
async def saved(db: Database) -> list[SavedSolution]:
    return await store.saved(db)


@router.post("/saved", response_model=SavedSolution)
async def save(payload: SaveRequest, db: Database) -> SavedSolution:
    return await store.save_solution(db, payload)


@router.post("/documents", response_model=KnowledgeSource, status_code=202)
async def documents(
    db: Database,
    kind: Annotated[str, Form()] = "upload",
    ingestion_format: Annotated[str, Form()] = "document",
    value: Annotated[str, Form()] = "",
    content: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> KnowledgeSource:
    if kind not in {"docs", "github", "upload"}:
        raise HTTPException(422, "Unsupported source kind")
    if ingestion_format not in {"document", "okf"}:
        raise HTTPException(422, "Unsupported ingestion format")
    if ingestion_format == "okf" and (kind == "github" or (file and not (file.filename or "").lower().endswith(".md"))):
        raise HTTPException(422, "OKF concepts must be uploaded as Markdown or pasted text")
    if len(value) > 2048 or "\x00" in value:
        raise HTTPException(422, "Invalid source title or URL")
    if file is not None and content is not None:
        raise HTTPException(422, "Provide a file or pasted content, not both")
    path = None
    remote_url = None
    status = "uploaded"
    error_message = None
    if kind == "github":
        remote_url = validated_remote_url(value)
        name = remote_url[:255]
        digest = hashlib.sha256(remote_url.encode()).hexdigest()
        status = "failed"
        error_message = "Remote URL ingestion is not configured; upload a file or paste documentation."
    else:
        if file is None and not content:
            raise HTTPException(400, "Provide document content or a file")
        if file:
            name = safe_filename(file.filename or "uploaded-document.md")
        else:
            # A document title may contain dots; it is not an uploaded filename.
            title = (value.strip() or "pasted-document")[:240]
            name = safe_filename(title if title.lower().endswith((".md", ".txt", ".rst")) else f"{title}.md")
        path, digest = await save_upload(name, file, content)
    try:
        result = await db.scalar(
            insert(SourceRecord)
            .values(
                id=uuid4(),
                owner_id=owner_id(db),
                ingestion_format=ingestion_format,
                name=name,
                source_type=kind,
                file_hash=digest,
                path=str(path) if path else None,
                url=remote_url,
                status=status,
                error_message=error_message,
            )
            .on_conflict_do_nothing(index_elements=["owner_id", "file_hash"])
            .returning(SourceRecord.id)
        )
        if result is None:
            record = (
                await db.scalars(
                    select(SourceRecord)
                    .where(SourceRecord.file_hash == digest, SourceRecord.owner_id == owner_id(db))
                    .with_for_update()
                )
            ).one()
            if record.ingestion_format != ingestion_format:
                raise HTTPException(409, "This content already exists with a different ingestion format")
            result = record.id
            if record.status == "failed" and path:
                # Retry from this upload, even when the previous file was lost.
                record.path = str(path)
                record.name = name
                record.status = "uploaded"
                record.error_message = None
            elif path:
                discard_upload(path)
                path = None
        await db.commit()
    except BaseException:
        await db.rollback()
        if path:
            discard_upload(path)
        raise
    return await source(result, db)


@router.post("/sources/{source_id}/retry-embedding", response_model=KnowledgeSource)
async def retry_embedding(source_id: UUID, db: Database) -> KnowledgeSource:
    from backend.services.embeddings import get_embedding_provider  # noqa: PLC0415

    if get_embedding_provider() is None:
        raise HTTPException(409, "Embedding pipeline is not configured")
    changed = await db.scalar(
        update(SourceRecord)
        .where(
            SourceRecord.id == source_id,
            SourceRecord.owner_id == owner_id(db),
            SourceRecord.status == "ready_for_embedding",
            SourceRecord.embedding_status == "failed",
        )
        .values(embedding_status="pending", embedding_error=None)
        .returning(SourceRecord.id)
    )
    if changed is None:
        raise HTTPException(404, "No retryable source found")
    await db.commit()
    return await source(changed, db)
