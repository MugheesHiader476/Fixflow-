"""Read-only local launch preflight. Never prints credentials or modifies application data."""

import asyncio
import json

from backend.config import get_settings
from backend.connectors.registry import configured, missing_configuration
from backend.db.session import close_database
from backend.schemas.connectors import Provider
from backend.services.readiness import database_readiness


async def check() -> dict[str, object]:
    settings = get_settings()
    readiness = await database_readiness("__launch_probe__")
    checks: dict[str, bool] = {
        "database_schema": readiness["status"] == "ok",
        "gateway_authentication": bool(settings.fixflow_api_token),
        "workspace_chat": settings.grok.configured,
        "public_origin": settings.connectors.public_url is not None,
    }
    return {
        "ready": all(checks.values()), "checks": checks,
        "connectors": {
            provider: {
                "configured": configured(provider, settings.connectors),
                "missing": missing_configuration(provider, settings.connectors),
                "callback_url": settings.connectors.public_url + f"/api/connectors/{provider}/callback",
            }
            for provider in PROVIDERS
        },
        "scope": "Local chat configuration and database prerequisites; no live model or OAuth verification",
        "operator_checks": [
            "Live Clerk sign-in and account isolation on the deployed origin",
            "Live consent and synchronization for every advertised connector",
            "TLS, private backend network, backup restoration, monitoring and capacity testing",
            "Representative extraction-quality and unsupported-format evaluation",
        ],
    }


PROVIDERS: tuple[Provider, ...] = ("gmail", "google_drive", "github", "slack")


async def main() -> int:
    try:
        result = await check()
    except (ValueError, OSError):
        result = {"ready": False, "error": "Invalid local configuration. Review environment settings privately."}
    finally:
        await close_database()
    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
