"""Malformed external responses/URLs must not escape as programmer errors."""

import httpx
import pytest

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError
from backend.connectors.http import ProviderHttp

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("error_field", [[], ["invalid_grant"], 17, None, True])
async def test_unexpected_provider_error_shape_is_safe(error_field: object) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json={"error": error_field}))
    with pytest.raises(ConnectorError) as failure:
        await ProviderHttp(ConnectorConfig(), transport).request(
            "GET", "https://www.googleapis.com/drive/v3/files", token="private-test-token"
        )
    assert failure.value.code == "provider_error"
    assert "private-test-token" not in str(failure.value)


@pytest.mark.parametrize("url", ["https://[broken", "https://www.googleapis.com:bad/path"])
async def test_malformed_provider_url_is_rejected_before_transport(url: str) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    with pytest.raises(ConnectorError) as failure:
        await ProviderHttp(ConnectorConfig(), httpx.MockTransport(respond)).request("GET", url)
    assert failure.value.code == "unsafe_url"
    assert not calls
