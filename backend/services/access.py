"""Authenticate the trusted Clerk gateway; never accept browser identity directly."""

import re
import secrets
from typing import Annotated

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings

LEGACY_OWNER = "__legacy__"
USER_ID = re.compile(r"user_[A-Za-z0-9_-]{1,190}\Z")


async def authenticated_owner(
    authorization: Annotated[str | None, Header()] = None,
    x_fixflow_user_id: Annotated[str | None, Header()] = None,
) -> str:
    token = get_settings().fixflow_api_token
    if token is None:
        raise HTTPException(503, "Backend authentication is not configured")
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not authorization.isascii() or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Authentication required")
    if x_fixflow_user_id is None or not USER_ID.fullmatch(x_fixflow_user_id):
        raise HTTPException(401, "Verified account identity required")
    return x_fixflow_user_id


def owner_id(session: AsyncSession) -> str:
    # Unauthenticated internal sessions are used only by workers/admin CLI, never HTTP routes.
    return str(session.info.get("owner_id", LEGACY_OWNER))
