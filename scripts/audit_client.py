"""Scan browser-delivered assets for configured secrets without printing their values."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from pathlib import Path
from typing import TypedDict

from dotenv import dotenv_values

PRIVATE_NAME = re.compile(r"(?:SECRET|TOKEN|PASSWORD|PRIVATE_KEY|VAULT_KEY|DATABASE_URL|API_KEY)", re.IGNORECASE)
PUBLIC_ALLOWED = {"NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"}
FILE_TYPES = {".js", ".json", ".html", ".css", ".map"}
CREDENTIAL_LITERAL = re.compile(
    r"(?:sk_(?:live|test)_[A-Za-z0-9]{20,}|sk-proj-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


class AuditResult(TypedDict):
    assets_checked: int
    secret_values_checked: int
    exposed_secrets: list[dict[str, str]]
    unsafe_public_variables: list[str]
    public_source_maps: list[str]
    suspicious_source_literals: list[str]
    passed: bool


def configured_secrets(root: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str | None] = {}
    for name in (".env", ".env.local", "backend/.env"):
        path = root / name
        if path.exists():
            values.update(dotenv_values(path))
    values.update(os.environ)
    secrets = {
        key: value
        for key, value in values.items()
        if value and len(value) >= 12 and PRIVATE_NAME.search(key) and key not in PUBLIC_ALLOWED
    }
    unsafe = sorted(key for key in secrets if key.startswith("NEXT_PUBLIC_"))
    return secrets, unsafe


def scan_client(root: Path, build: Path) -> AuditResult:
    secrets, unsafe = configured_secrets(root)
    assets = [path for path in (build / "static").rglob("*") if path.is_file() and path.suffix in FILE_TYPES]
    if not assets:
        raise ValueError("No browser build assets found; build the application before scanning")
    hits: list[dict[str, str]] = []
    for path in assets:
        payload = path.read_bytes()
        for name, value in secrets.items():
            variants = (value.encode(), json.dumps(value)[1:-1].encode(), base64.b64encode(value.encode()))
            if any(variant in payload for variant in variants):
                hits.append({"variable": name, "asset": str(path.relative_to(build))})
    maps = [str(path.relative_to(build)) for path in assets if path.suffix == ".map"]
    suspicious: list[str] = []
    for directory in ("src", "backend", "scripts", "public"):
        suspicious.extend(
            str(path.relative_to(root))
            for path in (root / directory).rglob("*")
            if path.is_file()
            and path.suffix in {".ts", ".tsx", ".py", ".js", ".mjs", ".json"}
            and "tests" not in path.relative_to(root).parts
            and CREDENTIAL_LITERAL.search(path.read_text(errors="replace"))
        )
    return {
        "assets_checked": len(assets),
        "secret_values_checked": len(secrets),
        "exposed_secrets": hits,
        "unsafe_public_variables": unsafe,
        "public_source_maps": maps,
        "suspicious_source_literals": suspicious,
        "passed": not hits and not unsafe and not maps and not suspicious,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--build", type=Path, default=Path(".next"))
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    result = scan_client(options.root.resolve(), options.build.resolve())
    payload = json.dumps(result, indent=2) + "\n"
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(payload)
    print(payload, end="")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
