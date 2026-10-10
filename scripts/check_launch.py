"""Read-only local launch preflight. Never prints credentials or modifies application data."""

import asyncio
import json

from backend.config import get_settings
from backend.db.session import close_database
from backend.services.embeddings import EmbeddingError
from backend.services.ollama import OllamaEmbeddingProvider
from backend.services.readiness import database_readiness
from backend.services.retrieval_config import retrieval_pin


async def check() -> dict[str, object]:
    settings = get_settings()
    readiness = await database_readiness("__launch_probe__")
    checks: dict[str, bool] = {
        "database_schema": readiness["status"] == "ok",
        "gateway_authentication": bool(settings.fixflow_api_token),
        "local_answer_model": readiness.get("answer_service") == "ready",
        "semantic_retrieval": settings.retrieval_mode == "dense" and settings.embedding_auto_process,
        "embedding_model": False,
        "public_origin": settings.connectors.public_url is not None,
    }
    try:
        pin = retrieval_pin()
        pin.check_runtime()
        async with asyncio.timeout(3):
            await OllamaEmbeddingProvider(
                settings.ollama_url or "", pin.model, pin.dimension, pin.model_digest, timeout=3,
            ).verify_identity()
        checks["embedding_model"] = True
    except (EmbeddingError, ValueError, TimeoutError):
        checks["embedding_model"] = False
    return {
        "ready": all(checks.values()), "checks": checks,
        "scope": "Local configuration, database and installed model identities only",
        "operator_checks": [
            "Live Clerk sign-in and account isolation on the deployed origin",
            "Live consent and synchronization for every advertised connector",
            "TLS, private backend network, backup restoration, monitoring and capacity testing",
            "Representative customer answer-quality and unsupported-format evaluation",
        ],
    }


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
