from email import utils

import pytest

from backend.connectors.core import ConnectorError
from backend.connectors.workspace import sender_address


@pytest.mark.parametrize("value", ["manager@example.test", "Manager <manager@example.test>"])
def test_reply_sender_uses_supported_strict_parser(value: str) -> None:
    assert sender_address(value) == "manager@example.test"


@pytest.mark.parametrize(
    "value",
    ["", "not-an-email", "Manager <manager@example.test>, Other <other@example.test>", "manager@example.test>"],
)
def test_malformed_or_multiple_senders_cannot_authorize_a_reply(value: str) -> None:
    with pytest.raises(ValueError):
        sender_address(value)


def test_unpatched_email_parser_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, "supports_strict_parsing", False)
    with pytest.raises(ConnectorError, match="strict address parser"):
        sender_address("Manager <manager@example.test>")
