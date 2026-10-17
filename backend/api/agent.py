"""Clerk-gateway authenticated workflows, exact-content email execution and private downloads."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AgentAction, AgentFile, AgentRun
from backend.db.session import get_session
from backend.schemas.agent import ActionApproval, ActionView, RunRequest, RunView
from backend.services import agent, agent_actions, agent_files
from backend.services.access import owner_id

router = APIRouter(prefix="/agent")
Database = Annotated[AsyncSession, Depends(get_session)]


@router.post("/runs", response_model=RunView, status_code=202)
async def create(payload: RunRequest, db: Database) -> RunView:
    return await agent.view(db, await agent.create(db, payload))


@router.get("/runs", response_model=list[RunView])
async def history(db: Database) -> list[RunView]:
    runs = list(
        await db.scalars(
            select(AgentRun).where(AgentRun.owner_id == owner_id(db)).order_by(AgentRun.created_at.desc()).limit(20)
        )
    )
    return [await agent.view(db, run) for run in runs]


@router.get("/runs/{identifier}", response_model=RunView)
async def status(identifier: UUID, db: Database) -> RunView:
    return await agent.view(db, await agent.owned(db, identifier))


@router.post("/runs/{identifier}/cancel", response_model=RunView)
async def cancel(identifier: UUID, db: Database) -> RunView:
    run = await agent.owned(db, identifier)
    await agent.cancel(db, run)
    return await agent.view(db, run)


@router.post("/actions/{identifier}/execute", response_model=ActionView)
async def execute(identifier: UUID, payload: ActionApproval, db: Database) -> ActionView:
    action = await db.scalar(
        select(AgentAction).where(AgentAction.id == identifier, AgentAction.owner_id == owner_id(db))
    )
    if action is None:
        raise HTTPException(404, "Action not found")
    ctx = agent.context(await agent.owned(db, action.run_id))
    await db.rollback()
    return await agent_actions.execute(ctx, identifier, payload.expected_hash)


@router.get("/files/{identifier}/download")
async def download(identifier: UUID, db: Database) -> Response:
    file = await db.scalar(select(AgentFile).where(AgentFile.id == identifier, AgentFile.owner_id == owner_id(db)))
    if file is None:
        raise HTTPException(404, "File not found")
    ctx = agent.context(await agent.owned(db, file.run_id))
    await db.rollback()
    data, filename, mime = await agent_files.download(ctx, identifier)
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
