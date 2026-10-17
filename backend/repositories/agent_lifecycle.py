"""Connector lifecycle invalidates workflow authority before any remote cleanup."""

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AgentAction, AgentFile, AgentRun


async def disable(db: AsyncSession, account_id: UUID, *, purge: bool = False) -> None:
    rows = list(
        await db.scalars(
            select(AgentRun).where(AgentRun.scope["accounts"].contains([{"id": str(account_id)}])).with_for_update()
        )
    )
    for run in rows:
        if run.status in {"queued", "running"} or purge:
            run.status, run.run_token, run.pending_calls = "cancelled", None, []
            run.error = "A connected source was disconnected or purged"
        if purge:
            run.messages, run.materials, run.answer = [], [], None
            await db.execute(delete(AgentFile).where(AgentFile.run_id == run.id))
            await db.execute(
                update(AgentAction)
                .where(AgentAction.run_id == run.id)
                .values(payload={}, status="blocked", error="Source data was purged; this draft is unavailable")
            )
