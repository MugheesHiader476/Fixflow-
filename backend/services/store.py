"""Read preserved private sessions/conversations and persist independent saved snapshots."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ChatEntry, DebugSession
from backend.db.models import SavedSolution as SavedRecord
from backend.schemas.models import (
    ChatMessage,
    Diagnosis,
    SavedSolution,
    SaveRequest,
    SessionSummary,
    diagnostic_text,
)
from backend.services.access import owner_id


class SessionNotFoundError(LookupError):
    """Raised when a request references an unknown debug session."""


def now() -> datetime:
    return datetime.now(UTC)


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
