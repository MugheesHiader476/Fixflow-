"""Authenticate and throttle APIs before parsing bodies; never trust forwarded identities."""

import math

from asyncpg import PostgresError
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.connectors.core import ConnectorError
from backend.security.rates import api_request, failed_gateway_authentication
from backend.services.access import authenticated_owner


class RequestGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        response: JSONResponse | None
        try:
            try:
                owner = await authenticated_owner(
                    headers.get(b"authorization", b"").decode("latin-1"),
                    headers.get(b"x-fixflow-user-id", b"").decode("latin-1"),
                )
            except HTTPException as error:
                if error.status_code == 401:
                    peer = scope.get("client")
                    await failed_gateway_authentication(str(peer[0]) if peer else "unknown")
                raise
            await api_request(owner, scope["path"], scope["method"])
            maximum = (52 if scope["path"] == "/api/documents" else 8) * 1024 * 1024
            if scope["path"].startswith(("/api/agent/", "/api/chat/messages")):
                maximum = 32 * 1024
            declared = headers.get(b"content-length")
            if declared is not None:
                try:
                    length = int(declared)
                except ValueError as error:
                    raise HTTPException(422, "Invalid Content-Length") from error
                if length < 0:
                    raise HTTPException(422, "Invalid Content-Length")
                if length > maximum:
                    raise HTTPException(413, "Request exceeds processing limit")
        except HTTPException as error:
            response = JSONResponse(
                {"success": False, "error": {"code": "HTTP_ERROR", "message": error.detail}},
                status_code=error.status_code,
            )
        except ConnectorError as error:
            response = JSONResponse(
                {"success": False, "error": {"code": "RATE_LIMITED", "message": "Request limit reached; retry later"}},
                status_code=429,
                headers={"Retry-After": str(max(1, math.ceil(error.retry_after or 60)))},
            )
        except (SQLAlchemyError, PostgresError, OSError):
            response = JSONResponse(
                {"success": False, "error": {"code": "SECURITY_UNAVAILABLE", "message": "Request checks unavailable"}},
                status_code=503,
            )
        else:
            response = None
        if response is not None:
            await response(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise HTTPException(413, "Request exceeds processing limit")
            return message

        await self.app(scope, limited_receive, send)
