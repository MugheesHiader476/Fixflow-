"""Authenticated connector API; public event ingress has a separate signature boundary."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError
from backend.connectors.events import receive
from backend.connectors.rates import reserve
from backend.connectors.registry import adapter
from backend.db.session import get_session, get_session_factory
from backend.schemas.connectors import (
    AccountResponse,
    ConnectResponse,
    DisconnectRequest,
    Provider,
    ProviderResponse,
    QueryResult,
    QuerySpec,
    ResourcePage,
    Selection,
    SyncRequest,
)
from backend.services import connectors
from backend.services.access import owner_id

router = APIRouter(prefix="/connectors")
events_router = APIRouter(prefix="/webhooks/connectors")
Database = Annotated[AsyncSession, Depends(get_session)]


async def user_rate(db: Database) -> None:
    await reserve("actions:" + owner_id(db), 60, get_settings().connectors.user_actions_per_minute)


@router.get("", response_model=list[ProviderResponse])
async def list_connectors(db: Database) -> list[ProviderResponse]:
    return await connectors.providers(db)


@router.post("/{provider}/connect", response_model=ConnectResponse, dependencies=[Depends(user_rate)])
async def connect(provider: Provider, db: Database) -> ConnectResponse:
    return ConnectResponse(authorization_url=await connectors.begin_connection(db, provider))


@router.get("/{provider}/callback", response_model=AccountResponse, dependencies=[Depends(user_rate)])
async def callback(
    provider: Provider,
    db: Database,
    state: Annotated[str, Query(min_length=20, max_length=200)],
    code: Annotated[str, Query(min_length=1, max_length=4096)],
) -> AccountResponse:
    account = await connectors.callback(db, provider, state, code)
    return await connectors.response(db, account)


@router.get("/{account_id}/status", response_model=AccountResponse)
async def status(account_id: UUID, db: Database) -> AccountResponse:
    return await connectors.response(db, await connectors.owned_account(db, account_id))


@router.get("/{account_id}/resources", response_model=ResourcePage, dependencies=[Depends(user_rate)])
async def resources(
    account_id: UUID, db: Database, cursor: Annotated[str | None, Query(max_length=4096)] = None
) -> ResourcePage:
    account = await connectors.owned_account(db, account_id)
    try:
        return await adapter(cast(Provider, account.provider), get_settings().connectors, account).list_resources(
            cursor
        )
    except ConnectorError as error:
        await db.rollback()
        await connectors.failure(account_id, error)
        raise


@router.post("/{account_id}/configure", response_model=AccountResponse, dependencies=[Depends(user_rate)])
async def configure(account_id: UUID, payload: Selection, db: Database) -> AccountResponse:
    # Resolve ownership before any failure reporting can touch an account.
    await connectors.owned_account(db, account_id)
    try:
        account = await connectors.configure(db, account_id, payload)
    except ConnectorError as error:
        await db.rollback()
        await connectors.failure(account_id, error)
        raise
    return await connectors.response(db, account)


@router.post("/{account_id}/sync", response_model=AccountResponse, status_code=202, dependencies=[Depends(user_rate)])
async def sync(account_id: UUID, payload: SyncRequest, db: Database) -> AccountResponse:
    account = await connectors.owned_account(db, account_id, lock=True)
    await connectors.enqueue(db, account, payload.mode)
    await db.commit()
    return await connectors.response(db, account)


@router.post("/{account_id}/disconnect", response_model=AccountResponse, dependencies=[Depends(user_rate)])
async def disconnect(account_id: UUID, payload: DisconnectRequest, db: Database) -> AccountResponse:
    return await connectors.response(db, await connectors.disconnect(db, account_id, payload.policy))


@router.post("/{account_id}/health", response_model=AccountResponse, dependencies=[Depends(user_rate)])
async def health(account_id: UUID, db: Database) -> AccountResponse:
    account = await connectors.owned_account(db, account_id)
    try:
        await adapter(cast(Provider, account.provider), get_settings().connectors, account).health_check()
    except ConnectorError as error:
        await db.rollback()
        await connectors.failure(account_id, error)
        raise
    await db.refresh(account)
    return await connectors.response(db, account)


@router.post("/{account_id}/query", response_model=QueryResult, dependencies=[Depends(user_rate)])
async def query(account_id: UUID, payload: QuerySpec, db: Database) -> QueryResult:
    account = await connectors.owned_account(db, account_id)
    try:
        return await adapter(cast(Provider, account.provider), get_settings().connectors, account).structured_query(
            payload, Selection.model_validate(account.configuration)
        )
    except ConnectorError as error:
        await db.rollback()
        await connectors.failure(account_id, error)
        raise


@events_router.post("/{provider}")
async def event(provider: Provider, request: Request) -> dict[str, object]:
    maximum = 1024 * 1024
    body = bytearray()
    async for block in request.stream():
        body.extend(block)
        if len(body) > maximum:
            raise HTTPException(413, "Event exceeds processing limit")
    try:
        async with get_session_factory()() as db:
            return await receive(db, provider, bytes(body), dict(request.headers), get_settings().connectors)
    except (KeyError, TypeError, ValueError) as error:
        raise ConnectorError("invalid_event", "Event payload is invalid") from error
