"""Bounded adapter for free local embeddings served by Ollama; no API key or paid provider."""

import asyncio
import json
import math
import time
from dataclasses import dataclass

import httpx

from backend.processing.embedding_input import BATCH_SIZE, DIMENSIONS, MODEL
from backend.schemas.evidence import EmbeddingConfig
from backend.security.policy import validate_disclosure
from backend.security.runtime import configured_secrets

MAX_RESPONSE_BYTES = 1024 * 1024
DIGEST_TTL_SECONDS = 300
_verified: dict[tuple[str, str], float] = {}


@dataclass
class EmbeddingUsage:
    calls: int = 0
    input_tokens: int | None = None
    completed: bool = False


def provider_failure(status: int) -> str:
    """Safe, actionable text for a local service HTTP status; never includes the response body."""
    if status == 404:
        return f"The local embedding model is not installed; run `ollama pull {MODEL}`"
    if status == 400:
        return "The local embedding service rejected the input (it may exceed the model context)"
    if status == 429:
        return "The local embedding service is busy; preparation will resume"
    return "Local embedding service error; preparation will retry"


def retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def normalized(values: object) -> list[float]:
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise ValueError("Invalid embedding dimension")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Invalid embedding values")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid embedding norm")
    return [float(value / norm) for value in values]


async def read_json(response: httpx.Response) -> object:
    response.raise_for_status()
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("Embedding response exceeds bounds")
    return json.loads(data)


async def verify_weights(client: httpx.AsyncClient, config: EmbeddingConfig) -> None:
    """Vectors from different weights must never share a profile; check the served digest."""
    key = (str(config.url), config.revision)
    if time.monotonic() - _verified.get(key, -math.inf) < DIGEST_TTL_SECONDS:
        return
    async with client.stream("GET", f"{config.url}/api/tags") as response:
        payload = await read_json(response)
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("Invalid local model listing")
    digests = [
        str(item.get("digest", ""))
        for item in models
        if isinstance(item, dict) and str(item.get("name", "")).split(":")[0] == MODEL
    ]
    if not digests:
        raise httpx.HTTPStatusError(
            "Embedding model missing", request=response.request, response=httpx.Response(404, request=response.request)
        )
    if not any(digest.startswith(config.revision) for digest in digests):
        raise ValueError("Local embedding weights changed; set EMBEDDINGS__REVISION and rebuild vectors")
    _verified[key] = time.monotonic()


async def embed(texts: list[str], config: EmbeddingConfig, usage: EmbeddingUsage) -> list[list[float]]:
    if not config.configured or not 1 <= len(texts) <= BATCH_SIZE:
        raise ValueError("Embedding is not configured or batch exceeds bounds")
    if any(not text.strip() or len(text.encode()) > 24_000 for text in texts):
        raise ValueError("Embedding input exceeds bounds")
    validate_disclosure(texts, configured_secrets())
    async with asyncio.timeout(config.timeout_seconds):
        async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
            await verify_weights(client, config)
            usage.calls += 1
            async with client.stream(
                "POST",
                f"{config.url}/api/embed",
                # truncate=false: oversized input fails instead of silently embedding a prefix.
                json={"model": MODEL, "input": texts, "truncate": False, "keep_alive": "10m"},
            ) as response:
                payload = await read_json(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("embeddings"), list):
        raise ValueError("Missing embedding batch")
    embeddings = payload["embeddings"]
    if len(embeddings) != len(texts):
        raise ValueError("Embedding batch count mismatch")
    # Ollama returns vectors in input order; validate the whole mapping before returning any vector.
    vectors = [normalized(item) for item in embeddings]
    count = payload.get("prompt_eval_count")
    if type(count) is int and count >= 0:
        usage.input_tokens = count
    usage.completed = True
    return vectors
