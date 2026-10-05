from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
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
    force: Annotated[bool, Form()] = False,
    update_source_id: Annotated[UUID | None, Form()] = None,
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
        registered_id = update_source_id or uuid4()
        registration: dict[str, object] = {
            "source_id": str(registered_id),
            "filename": name,
            "mime_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "extension": path.suffix.lower() if path else None,
            "size": (await asyncio.to_thread(path.stat)).st_size if path else None,
            "sha256": digest,
            "ingested_at": datetime.now(UTC).isoformat(),
            "version": 1,
            "original_uri": f"source:{registered_id}",
            "force": force,
        }
        if update_source_id:
            if path is None:
                raise HTTPException(422, "Source updates require uploaded or pasted content")
            lock_key = int.from_bytes(update_source_id.bytes[:8], "big", signed=True)
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock_key))):
                raise HTTPException(409, "Source is currently processing")
            existing = await db.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == update_source_id, SourceRecord.owner_id == owner_id(db))
                .with_for_update()
            )
            if existing is None:
                raise HTTPException(404, "Source not found")
            if existing.connector_account_id is not None:
                raise HTTPException(409, "Connected sources must be updated through connector synchronization")
            duplicate = await db.scalar(
                select(SourceRecord.id).where(
                    SourceRecord.owner_id == owner_id(db),
                    SourceRecord.file_hash == digest,
                    SourceRecord.external_identity.is_(None),
                    SourceRecord.id != update_source_id,
                )
            )
            if duplicate:
                raise HTTPException(409, "This content already belongs to another source")
            if existing.file_hash == digest and existing.ingestion_format != ingestion_format:
                raise HTTPException(409, "This content already exists with a different ingestion format")
            needs_upgrade = (
                existing.status in {"ready_for_embedding", "embedding", "indexed"}
                and existing.ingestion_metadata.get("pipeline_contract_version") != 2
            )
            if existing.file_hash == digest and existing.status != "failed" and not force and not needs_upgrade:
                discard_upload(path)
                path = None
            else:
                prior = existing.ingestion_metadata.get("registration", existing.ingestion_metadata)
                if isinstance(prior, dict):
                    registration["version"] = int(prior.get("version", 1)) + int(prior.get("sha256") != digest)
                existing.path, existing.name, existing.file_hash = str(path), name, digest
                existing.ingestion_format, existing.status = ingestion_format, "uploaded"
                existing.source_type, existing.url = kind, None
                existing.error_message = None
                existing.ingestion_metadata = registration
            await db.commit()
            return await source(update_source_id, db)
        result = await db.scalar(
            insert(SourceRecord)
            .values(
                id=registered_id,
                owner_id=owner_id(db),
                ingestion_format=ingestion_format,
                name=name,
                source_type=kind,
                file_hash=digest,
                path=str(path) if path else None,
                url=remote_url,
                status=status,
                error_message=error_message,
                ingestion_metadata=registration,
            )
            .on_conflict_do_nothing(
                index_elements=["owner_id", "file_hash"], index_where=SourceRecord.external_identity.is_(None)
            )
            .returning(SourceRecord.id)
        )
        if result is None:
            record = (
                await db.scalars(
                    select(SourceRecord)
                    .where(
                        SourceRecord.file_hash == digest,
                        SourceRecord.owner_id == owner_id(db),
                        SourceRecord.external_identity.is_(None),
                    )
                    .with_for_update()
                )
            ).one()
            if record.ingestion_format != ingestion_format:
                raise HTTPException(409, "This content already exists with a different ingestion format")
            result = record.id
            needs_upgrade = (
                record.status in {"ready_for_embedding", "embedding", "indexed"}
                and record.ingestion_metadata.get("pipeline_contract_version") != 2
            )
            if (record.status == "failed" or force or needs_upgrade) and path:
                lock_key = int.from_bytes(record.id.bytes[:8], "big", signed=True)
                if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock_key))):
                    raise HTTPException(409, "Source is currently processing")
                # Retry from this upload, even when the previous file was lost.
                prior = record.ingestion_metadata.get("registration", record.ingestion_metadata)
                registration.update(source_id=str(record.id), original_uri=f"source:{record.id}")
                if isinstance(prior, dict):
                    registration["version"] = int(prior.get("version", 1)) + int(prior.get("sha256") != digest)
                record.path = str(path)
                record.name = name
                record.status = "uploaded"
                record.error_message = None
                record.ingestion_metadata = registration
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
