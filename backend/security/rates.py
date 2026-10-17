"""Durable account quotas and bounded anonymous failure buckets in PostgreSQL."""

import hashlib

from backend.config import get_settings
from backend.connectors.rates import reserve


async def failed_gateway_authentication(peer: str) -> None:
    config = get_settings().security
    await reserve("security:auth-failure:global", 60, config.auth_failures_global_per_minute)
    # Fixed cardinality prevents unauthenticated callers creating unbounded DB keys.
    # Only failed gateway authentication uses this bucket; valid owners bypass it.
    bucket = int.from_bytes(hashlib.sha256(peer.encode()).digest()[:2], "big") % 2048
    await reserve("security:auth-failure:" + str(bucket), 60, config.auth_failures_per_minute)


async def api_request(owner: str, path: str, method: str) -> None:
    config = get_settings().security
    if method == "POST" and path.startswith("/api/agent/runs/") and path.endswith("/cancel"):
        # A spent write budget must not prevent stopping an in-flight external action.
        await reserve("security:cancel:" + owner, 60, config.sensitive_requests_per_minute)
        return
    write = method not in {"GET", "HEAD"}
    await reserve(
        "security:api:" + ("write:" if write else "read:") + owner,
        60,
        config.write_requests_per_minute if write else config.read_requests_per_minute,
    )
    if path.startswith("/api/agent/") and (path.endswith("/execute") or path.endswith("/download")):
        await reserve("security:sensitive:" + owner, 60, config.sensitive_requests_per_minute)


async def email_dispatch(owner: str) -> None:
    await reserve("security:email-dispatch:" + owner, 3600, get_settings().security.email_dispatches_per_hour)
