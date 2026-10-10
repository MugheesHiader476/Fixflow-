"""Persist sessions and conversations independently of the diagnosis provider."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ChatEntry, DebugSession
from backend.db.models import SavedSolution as SavedRecord
from backend.schemas.models import (
    ChatMessage,
    DebugRequest,
    Diagnosis,
    GroundedAnswer,
    RagDetails,
    SavedSolution,
    SaveRequest,
    SessionSummary,
    SourceReference,
)
from backend.services.access import owner_id
from backend.services.diagnosis import DiagnosisProvider, diagnostic_text
from backend.services.generation import GenerationError
from backend.services.retrieval import evidence_is_current, generation_evidence, retrieve_sources


class SessionNotFoundError(LookupError):
    """Raised when a request references an unknown debug session."""


def now() -> datetime:
    return datetime.now(UTC)


async def diagnose(db: AsyncSession, payload: DebugRequest, provider: DiagnosisProvider) -> Diagnosis:
    query = diagnostic_text(payload)
    sources = await retrieve_sources(db, query, limit=12 if provider.generation == "model" else 5)
    if provider.generation == "model":
        sources = await generation_evidence(db, sources, expand_context=True)
    draft = await provider.diagnose(payload, sources)
    if provider.generation == "model" and not await evidence_is_current(db, sources):
        raise GenerationError("Your sources changed while preparing the answer. Please ask again.")
    cited_ids = {citation.id for citation in draft.answer.citations} if draft.answer else set()
    if draft.answer:
        sources = [source.model_copy(update={"used": source.id in cited_ids}) for source in sources]
    session_id = uuid4()
    diagnosis = Diagnosis(
        **draft.model_dump(),
        sessionId=str(session_id),
        request=payload,
        generation=provider.generation,
        sources=sources,
        rag=RagDetails(
            query=query,
            expansions=[],
            retrieved=len(sources),
            reranked=0,
            sourcesUsed=len({source.source_id or source.title for source in sources if source.used}),
            topChunks=[],
            retrievalMethod="dense" if db.info.get("retrieval_method") == "dense" else "keyword",
        ),
    )
    db.add(DebugSession(id=session_id, owner_id=owner_id(db), diagnosis=diagnosis.model_dump(mode="json")))
    await db.commit()
    return diagnosis


async def messages(db: AsyncSession, session_id: UUID) -> list[ChatMessage]:
    if await get_session(db, session_id) is None:
        raise SessionNotFoundError("Session not found")
    records = await db.scalars(
        select(ChatEntry)
        .where(ChatEntry.session_id == session_id)
        .order_by(
            ChatEntry.created_at,
            case((ChatEntry.payload["role"].astext == "user", 0), else_=1),
            ChatEntry.id,
        )
    )
    return [ChatMessage.model_validate(record.payload) for record in records]


async def chat(db: AsyncSession, session_id: UUID, question: str, provider: DiagnosisProvider) -> ChatMessage:
    diagnosis = await get_session(db, session_id)
    if diagnosis is None:
        raise SessionNotFoundError("Session not found")
    history = await messages(db, session_id)
    # Carry user questions into retrieval so references such as "that policy" resolve.
    # Previous generated answers are not used as evidence or query expansions.
    prior = diagnostic_text(diagnosis.request)[:1500] if diagnosis.request else ""
    recent = [message.text[:500] for message in history[-4:] if message.role == "user"]
    query = "\n".join([question, prior, *recent]) if provider.generation == "model" else question
    evidence = await retrieve_sources(db, query, limit=12 if provider.generation == "model" else 3)
    if provider.generation == "model":
        evidence = await generation_evidence(db, evidence, expand_context=True)
    answer = await provider.reply(question, diagnosis, history, evidence)
    if provider.generation == "model" and not await evidence_is_current(db, evidence):
        raise GenerationError("Your sources changed while preparing the answer. Please ask again.")
    grounded = answer if isinstance(answer, GroundedAnswer) else None
    references = {
        source.id: SourceReference(
            id=source.id, title=source.title, type=source.type, source_id=source.source_id,
            url=source.url, excerpt=source.excerpt, location=source.location,
        )
        for source in evidence
    }
    reply = ChatMessage(
        id=str(uuid4()), role="fixflow", text=grounded.text if grounded else str(answer), answer=grounded,
        sources=list(grounded.citations) if grounded else list(references.values()),
    )
    user = ChatMessage(id=str(uuid4()), role="user", text=question)
    db.add_all(
        [
            ChatEntry(session_id=session_id, payload=message.model_dump(mode="json"), created_at=now())
            for message in (user, reply)
        ]
    )
    await db.commit()
    return reply


async def sessions(db: AsyncSession) -> list[SessionSummary]:
    records = await db.scalars(
        select(DebugSession)
        .where(DebugSession.owner_id == owner_id(db))
        .order_by(DebugSession.created_at.desc(), DebugSession.id)
    )
    result = []
    for record in records:
        diagnosis = Diagnosis.model_validate(record.diagnosis)
        description = diagnostic_text(diagnosis.request) if diagnosis.request else diagnosis.rootCause
        title = description.splitlines()[0][:100] if description else "Knowledge session"
        result.append(
            SessionSummary(
                id=str(record.id),
                title=title,
                technology=diagnosis.detected,
                createdAt=record.created_at,
                status="unresolved" if diagnosis.status == "no-cause" else "in-progress",
                confidence=diagnosis.confidence,
                errorMessage=description[:500],
            )
        )
    return result


async def get_session(db: AsyncSession, session_id: UUID) -> Diagnosis | None:
    record = await db.scalar(
        select(DebugSession).where(DebugSession.id == session_id, DebugSession.owner_id == owner_id(db))
    )
    return Diagnosis.model_validate(record.diagnosis) if record else None


async def saved(db: AsyncSession) -> list[SavedSolution]:
    records = await db.scalars(
        select(SavedRecord)
        .where(SavedRecord.owner_id == owner_id(db))
        .order_by(SavedRecord.created_at.desc(), SavedRecord.id)
    )
    return [SavedSolution.model_validate(record.payload) for record in records]


async def save_solution(db: AsyncSession, payload: SaveRequest) -> SavedSolution:
    solution = SavedSolution(id=str(uuid4()), **payload.model_dump(), savedAt=now())
    db.add(SavedRecord(id=UUID(solution.id), owner_id=owner_id(db), payload=solution.model_dump(mode="json")))
    await db.commit()
    return solution
