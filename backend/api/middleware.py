"""Reject unauthenticated and oversized API bodies before multipart parsing."""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.services.access import authenticated_owner


class RequestGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        try:
            await authenticated_owner(
                headers.get(b"authorization", b"").decode("latin-1"),
                headers.get(b"x-fixflow-user-id", b"").decode("latin-1"),
            )
            maximum = (52 if scope["path"] == "/api/documents" else 8) * 1024 * 1024
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
            await JSONResponse(
                {"success": False, "error": {"code": "HTTP_ERROR", "message": error.detail}},
                status_code=error.status_code,
            )(scope, receive, send)
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
