"""Seed historical JSONB snapshots directly; the removed answer endpoints never create them."""

from uuid import uuid4

from backend.db.models import ChatEntry, DebugSession
from backend.db.session import get_session_factory
from backend.schemas.models import ChatMessage, DebugRequest, Diagnosis, RagDetails


async def seed_history() -> tuple[dict[str, object], dict[str, object]]:
    identifier = uuid4()
    diagnosis = Diagnosis(
        sessionId=str(identifier),
        status="no-cause",
        detected=["Python"],
        rootCause="Historical finding",
        whyThisHappens="Preserved snapshot",
        recommendedFix=[],
        alternatives=[],
        sources=[],
        generation="disabled",
        request=DebugRequest(error="Original question"),
        rag=RagDetails(query="Original question", expansions=[], retrieved=0, reranked=0, sourcesUsed=0, topChunks=[]),
    )
    reply = ChatMessage(id=str(uuid4()), role="fixflow", text="Preserved historical message")
    async with get_session_factory().begin() as db:
        db.add(DebugSession(id=identifier, owner_id="user_test", diagnosis=diagnosis.model_dump(mode="json")))
        await db.flush()
        db.add(ChatEntry(session_id=identifier, payload=reply.model_dump(mode="json")))
    return diagnosis.model_dump(mode="json"), reply.model_dump(mode="json")
