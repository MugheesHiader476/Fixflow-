import json
from pathlib import Path

import pytest

from scripts.audit_client import scan_client


def test_scan_detects_leaked_private_value_without_reporting_it(tmp_path: Path) -> None:
    secret = "canary-do-not-expose-this-private-value"
    (tmp_path / ".env.local").write_text(f"FIXFLOW_API_TOKEN={secret}\n")
    assets = tmp_path / ".next/static/chunks"
    assets.mkdir(parents=True)
    (assets / "leak.js").write_text(f"window.secret={json.dumps(secret)}")
    result = scan_client(tmp_path, tmp_path / ".next")
    assert result["passed"] is False
    assert {"variable": "FIXFLOW_API_TOKEN", "asset": "static/chunks/leak.js"} in result["exposed_secrets"]
    assert secret not in json.dumps(result)


def test_public_clerk_key_is_allowed_but_public_private_settings_fail(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text(
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_public-identity\nNEXT_PUBLIC_API_KEY=private-value-exposed\n"
    )
    assets = tmp_path / ".next/static"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("/* no private value present */")
    result = scan_client(tmp_path, tmp_path / ".next")
    assert result["unsafe_public_variables"] == ["NEXT_PUBLIC_API_KEY"]
    assert result["passed"] is False


def test_missing_build_is_not_a_false_negative(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No browser build assets"):
        scan_client(tmp_path, tmp_path / ".next")
