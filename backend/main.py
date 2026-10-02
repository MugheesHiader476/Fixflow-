from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit

from asyncpg import PostgresError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.connectors import events_router
from backend.api.connectors import router as connector_router
from backend.api.middleware import RequestGuardMiddleware
from backend.api.routes import router
from backend.config import get_settings
from backend.connectors.core import ConnectorError
from backend.db.session import close_database
from backend.services.connector_sync import connector_worker
from backend.services.embeddings import embedding_worker
from backend.services.ingestion import ingestion_worker
from backend.services.readiness import database_readiness

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = asyncio.create_task(ingestion_worker(), name="document-ingestion")
    embeddings = asyncio.create_task(embedding_worker(), name="document-embeddings")
    connectors = asyncio.create_task(connector_worker(), name="connector-sync")
    try:
        yield
    finally:
        embeddings.cancel()
        worker.cancel()
        connectors.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        with suppress(asyncio.CancelledError):
            await embeddings
        with suppress(asyncio.CancelledError):
            await connectors
        await close_database()


app = FastAPI(title="FixFlow API", version="0.2.0", lifespan=lifespan)


def configured_origins(value: str) -> list[str]:
    origins: list[str] = []
    for candidate in value.split(","):
        origin = candidate.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(f"Invalid FRONTEND_ORIGINS entry: {origin!r}")
        if origin not in origins:
            origins.append(origin)
    if not origins:
        raise ValueError("FRONTEND_ORIGINS must contain at least one HTTP(S) origin")
    return origins


app.add_middleware(RequestGuardMiddleware)

origins = configured_origins(get_settings().frontend_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
    return error_response("HTTP_ERROR", str(error.detail), error.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    return error_response("VALIDATION_ERROR", "Request validation failed", 422)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, error: Exception) -> JSONResponse:
    logger.error(
        "Unhandled error while processing %s %s (%s)",
        request.method,
        request.url.path,
        type(error).__name__,
    )
    return error_response("INTERNAL_ERROR", "Internal server error", 500)


@app.exception_handler(SQLAlchemyError)
@app.exception_handler(PostgresError)
async def database_error(_: Request, __: SQLAlchemyError | PostgresError) -> JSONResponse:
    return error_response("DATABASE_UNAVAILABLE", "Database operation unavailable", 503)


@app.get("/health")
async def health() -> JSONResponse:
    result = await database_readiness()
    return JSONResponse(
        status_code=200 if result["status"] == "ok" else 503,
        content=result,
    )


app.include_router(router, prefix="/api")
app.include_router(connector_router, prefix="/api")
app.include_router(events_router)


@app.exception_handler(ConnectorError)
async def connector_error(_: Request, error: ConnectorError) -> JSONResponse:
    status = (
        429
        if error.code == "rate_limited"
        else 404
        if error.code == "not_found"
        else 503
        if error.code in {"not_configured", "provider_unavailable"}
        else 401
        if error.code == "invalid_event"
        else 409
        if error.code == "processing"
        else 422
    )
    result = error_response(error.code.upper(), str(error), status)
    if error.retry_after:
        result.headers["Retry-After"] = str(max(1, int(error.retry_after)))
    return result
