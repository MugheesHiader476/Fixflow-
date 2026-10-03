"""Bounded HTTPS requests; errors and logs contain neither provider bodies nor credentials."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import NoReturn
from urllib.parse import urljoin, urlsplit

import httpx

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError, record, records

logger = logging.getLogger(__name__)


def reject_nonfinite(_: str) -> NoReturn:
    raise ValueError("Non-finite JSON values are unsupported")


HOSTS = {
    "gmail.googleapis.com",
    "www.googleapis.com",
    "oauth2.googleapis.com",
    "openidconnect.googleapis.com",
    "api.github.com",
    "github.com",
    "slack.com",
    "api.slack.com",
    "files.slack.com",
    "www.google.com",
}


def retry_seconds(response: httpx.Response) -> float:
    header = response.headers.get("Retry-After")
    try:
        if header is not None:
            value = (
                float(header)
                if header.replace(".", "", 1).isdigit()
                else (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
            )
            return min(86400, max(1, value))
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            return min(86400, max(1, float(reset) - time.time()))
    except (ValueError, TypeError, OverflowError):
        pass
    return 60


class ProviderHttp:
    def __init__(self, config: ConnectorConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config, self.transport = config, transport

    async def request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        params: dict[str, str | int] | None = None,
        data: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        basic: tuple[str, str] | None = None,
        raw: bool = False,
        _redirects: int = 0,
    ) -> bytes | dict[str, object] | list[dict[str, object]]:
        target = urlsplit(url)
        if (
            target.scheme != "https"
            or target.hostname not in HOSTS
            or target.username
            or target.password
            or target.fragment
            or target.port not in {None, 443}
        ):
            raise ConnectorError("unsafe_url", "Provider URL is not authorized")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if token:
            request_headers["Authorization"] = "Bearer " + token
        for attempt in range(3):
            try:
                async with (
                    httpx.AsyncClient(
                        timeout=self.config.request_timeout_seconds, follow_redirects=False, transport=self.transport
                    ) as client,
                    client.stream(
                        method, url, headers=request_headers, params=params, data=data, json=payload, auth=basic
                    ) as response,
                ):
                    if response.status_code in {301, 302, 303, 307, 308} and method == "GET":
                        destination = urljoin(url, response.headers.get("Location", ""))
                        redirected = urlsplit(destination)
                        same_provider = redirected.hostname == target.hostname or (
                            raw
                            and target.hostname in {"slack.com", "files.slack.com"}
                            and redirected.hostname in {"slack.com", "files.slack.com"}
                        )
                        if _redirects >= 3 or not response.headers.get("Location") or not same_provider:
                            raise ConnectorError("unsafe_url", "Provider redirect is not authorized")
                        return await self.request(
                            "GET",
                            destination,
                            token=token,
                            headers=headers,
                            basic=basic,
                            raw=raw,
                            _redirects=_redirects + 1,
                        )
                    if response.status_code in {400, 403}:
                        error_body = bytearray()
                        async for block in response.aiter_bytes():
                            error_body.extend(block)
                            if len(error_body) > 65536:
                                break
                        try:
                            error_json = record(json.loads(error_body))
                            provider_error = error_json.get("error")
                            message = error_json.get("message")
                            if (
                                response.status_code == 403
                                and isinstance(message, str)
                                and (
                                    "secondary rate limit" in message.casefold()
                                    or "api rate limit exceeded" in message.casefold()
                                )
                            ):
                                raise ConnectorError(
                                    "rate_limited",
                                    "Provider quota reached; synchronization will resume",
                                    retry_seconds(response),
                                )
                            if isinstance(provider_error, dict):
                                reasons = [item.get("reason") for item in records(provider_error.get("errors", []))]
                                if any(
                                    reason in {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}
                                    for reason in reasons
                                ):
                                    raise ConnectorError(
                                        "rate_limited",
                                        "Provider quota reached; synchronization will resume",
                                        retry_seconds(response),
                                    )
                            elif provider_error in {"invalid_grant", "invalid_token"}:
                                raise ConnectorError(
                                    "reauth_required", "Provider authorization expired or was revoked; reconnect"
                                )
                        except (ValueError, RecursionError):
                            pass
                    if response.status_code == 410:
                        raise ConnectorError(
                            "stale_cursor", "Provider change cursor expired; reconciliation is required"
                        )
                    if response.status_code == 429 or (
                        response.status_code == 403
                        and (
                            response.headers.get("X-RateLimit-Remaining") == "0" or response.headers.get("Retry-After")
                        )
                    ):
                        raise ConnectorError(
                            "rate_limited", "Provider rate limit reached; sync will resume", retry_seconds(response)
                        )
                    if response.status_code == 401:
                        raise ConnectorError(
                            "reauth_required", "Authorization expired or was revoked; reconnect this account"
                        )
                    if response.status_code in {403, 404}:
                        raise ConnectorError(
                            "inaccessible" if response.status_code == 403 else "not_found",
                            "The selected resource is no longer accessible",
                        )
                    if response.status_code >= 500:
                        raise ConnectorError("provider_unavailable", "Provider is temporarily unavailable", 30)
                    if not 200 <= response.status_code < 300:
                        raise ConnectorError("provider_error", "Provider rejected the request")
                    body = bytearray()
                    async for block in response.aiter_bytes():
                        body.extend(block)
                        if len(body) > self.config.max_response_bytes:
                            raise ConnectorError("too_large", "Provider resource exceeds the configured download limit")
                    if raw:
                        return bytes(body)
                    if not body:
                        return {}
                    try:
                        decoded = json.loads(body, parse_constant=reject_nonfinite)
                        return records(decoded) if isinstance(decoded, list) else record(decoded)
                    except (ValueError, RecursionError) as error:
                        raise ConnectorError("invalid_response", "Provider returned invalid JSON") from error
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == 2:
                    raise ConnectorError(
                        "provider_unavailable", "Provider request failed; synchronization can retry", 30
                    ) from error
            except ConnectorError as error:
                if error.code != "provider_unavailable" or attempt == 2:
                    raise
            logger.info(json.dumps({"event": "connector_retry", "host": target.hostname, "attempt": attempt + 1}))
            await asyncio.sleep(0.25 * 2**attempt)
        raise ConnectorError("provider_unavailable", "Provider is temporarily unavailable", 30)
