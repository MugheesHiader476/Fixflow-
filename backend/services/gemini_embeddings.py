"""Bounded direct REST adapter. One Content per passage avoids batch aggregation."""

import asyncio
import json
import math
from dataclasses import dataclass

import httpx

from backend.processing.embedding_input import BATCH_SIZE, DIMENSIONS, MODEL
from backend.schemas.evidence import GeminiConfig
from backend.security.policy import validate_disclosure
from backend.security.runtime import configured_secrets


@dataclass
class EmbeddingUsage:
    calls: int = 0
    input_tokens: int | None = None
    completed: bool = False


def normalized(values: object) -> list[float]:
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise ValueError("Invalid embedding dimension")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Invalid embedding values")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid embedding norm")
    return [float(value / norm) for value in values]


async def embed(texts: list[str], config: GeminiConfig, usage: EmbeddingUsage) -> list[list[float]]:
    if not config.configured or config.api_key is None or not 1 <= len(texts) <= BATCH_SIZE:
        raise ValueError("Embedding is not configured or batch exceeds bounds")
    if any(not text.strip() or len(text.encode()) > 24_000 for text in texts):
        raise ValueError("Embedding input exceeds bounds")
    validate_disclosure(texts, configured_secrets())
    body = {
        "requests": [
            {
                "model": "models/" + MODEL,
                "content": {"parts": [{"text": text}]},
                "embedContentConfig": {"outputDimensionality": DIMENSIONS, "autoTruncate": False},
            }
            for text in texts
        ]
    }
    async with asyncio.timeout(config.timeout_seconds):
        async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
            usage.calls += 1
            async with client.stream(
                "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:batchEmbedContents",
                headers={"x-goog-api-key": config.api_key.get_secret_value()},
                json=body,
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 512 * 1024:
                        raise ValueError("Embedding response exceeds bounds")
    payload = json.loads(data)
    if not isinstance(payload, dict) or not isinstance(payload.get("embeddings"), list):
        raise ValueError("Missing embedding batch")
    embeddings = payload["embeddings"]
    if len(embeddings) != len(texts):
        raise ValueError("Embedding batch count mismatch")
    # This synchronous API guarantees request order (unlike async batch output).
    # Validate the entire positional mapping before returning any vector.
    vectors = []
    for item in embeddings:
        if not isinstance(item, dict):
            raise ValueError("Invalid embedding")
        statistics = item.get("statistics", {})
        if not isinstance(statistics, dict) or statistics.get("truncated"):
            raise ValueError("Provider truncated embedding input")
        vectors.append(normalized(item.get("values")))
    metadata = payload.get("usageMetadata")
    count = metadata.get("promptTokenCount") if isinstance(metadata, dict) else None
    if type(count) is int and count >= 0:
        usage.input_tokens = count
    usage.completed = True
    return vectors
