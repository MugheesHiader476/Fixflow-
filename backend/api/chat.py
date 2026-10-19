"""Simple owner-scoped chat over the existing durable, permission-checked assistant."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AgentRun
from backend.db.session import get_session
from backend.schemas.agent import ChatRequest, RunRequest, RunView
from backend.services import agent
from backend.services.access import owner_id

router = APIRouter(prefix="/chat/messages")
Database = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=RunView, status_code=202)
async def send(payload: ChatRequest, db: Database) -> RunView:
    # App scope and read-only action policy are server choices, never model arguments.
    request = RunRequest(question=payload.question, parent_run_id=payload.parent_message_id)
    return await agent.view(db, await agent.create(db, request))


@router.get("", response_model=list[RunView])
async def history(db: Database) -> list[RunView]:
    messages = await db.scalars(
        select(AgentRun).where(AgentRun.owner_id == owner_id(db)).order_by(AgentRun.created_at.desc()).limit(20)
    )
    return [await agent.view(db, message) for message in messages]


@router.get("/{identifier}", response_model=RunView)
async def status(identifier: UUID, db: Database) -> RunView:
    return await agent.view(db, await agent.owned(db, identifier))


@router.get("/{identifier}/thread", response_model=list[RunView])
async def thread(identifier: UUID, db: Database) -> list[RunView]:
    messages = await agent.ancestors(db, await agent.owned(db, identifier), limit=20)
    return [await agent.view(db, message) for message in messages]


@router.post("/{identifier}/cancel", response_model=RunView)
async def cancel(identifier: UUID, db: Database) -> RunView:
    message = await agent.owned(db, identifier)
    await agent.cancel(db, message)
    return await agent.view(db, message)
