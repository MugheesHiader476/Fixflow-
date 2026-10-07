"""Native, loopback-only Ollama transport with pinned model identity and bounded output."""

import json

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.config import Settings
from backend.repositories.vectors import VectorRepository
from backend.services.embeddings import EmbeddingError


class OllamaResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    model: str
    embeddings: list[list[float]]


class OllamaEmbeddingProvider:
    def __init__(self, url: str, model: str, dimension: int, model_digest: str, timeout: float = 120) -> None:
        validated = Settings.validate_ollama_url(url)
        if validated is None:
            raise ValueError("Local embedding endpoint is required")
        self.url = validated
        self.model = model
        self.dimension = dimension
        self.model_digest = model_digest
        self.timeout = timeout

    async def request(self, method: str, path: str, payload: dict[str, object] | None = None) -> object:
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False) as client,
                client.stream(method, self.url + path, json=payload) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 16 * 1024 * 1024:
                        raise ValueError("Ollama response exceeds limit")
                return json.loads(body)
        except (httpx.HTTPError, ValueError, TypeError, RecursionError) as error:
            raise EmbeddingError("Local embedding service unavailable or invalid response") from error

    async def verify_identity(self) -> None:
        payload = await self.request("GET", "/api/tags")
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list) or not any(
            isinstance(item, dict) and item.get("name") == self.model and item.get("digest") == self.model_digest
            for item in models
        ):
            raise EmbeddingError("Local model is missing or its pinned digest changed")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise EmbeddingError("Embedding input must be nonempty")
        await self.verify_identity()
        payload = await self.request(
            "POST",
            "/api/embed",
            {"model": self.model, "input": texts, "truncate": False, "keep_alive": "5m"},
        )
        try:
            parsed = OllamaResponse.model_validate(payload)
            if parsed.model != self.model or len(parsed.embeddings) != len(texts):
                raise ValueError("Ollama response does not match batch/model")
            for vector in parsed.embeddings:
                VectorRepository.validate_vector(vector, self.dimension)
        except (ValidationError, ValueError, TypeError) as error:
            raise EmbeddingError("Local embedding service returned invalid vectors") from error
        await self.verify_identity()
        return parsed.embeddings
