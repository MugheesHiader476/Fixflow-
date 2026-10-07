"""Versioned model formatting; canonical/retrieval content is never rewritten."""

from dataclasses import dataclass
from typing import Literal

from backend.schemas.pipeline import digest

FormatVersion = Literal["plain-v1", "qwen-v1", "gemma-v1", "nomic-v1"]


@dataclass(frozen=True)
class ModelProfile:
    tag: str
    dimension: int
    version: FormatVersion

    def document(self, text: str, title: str = "none") -> str:
        if self.version == "gemma-v1":
            # Large headings are already evidence chunks; do not repeat an unbounded label.
            label = title if title and len(title.encode("utf-8")) <= 256 else "none"
            return f"title: {label} | text: {text}"
        if self.version == "nomic-v1":
            return "search_document: " + text
        return text

    def query(self, text: str) -> str:
        if self.version == "qwen-v1":
            return (
                "Instruct: Given a debugging question, retrieve relevant technical documentation and code passages.\n"
                "Query:" + text
            )
        if self.version == "gemma-v1":
            return "task: search result | query: " + text
        if self.version == "nomic-v1":
            return "search_query: " + text
        return text


PROFILES = {
    p.tag: p
    for p in (
        ModelProfile("qwen3-embedding:0.6b", 1024, "qwen-v1"),
        ModelProfile("embeddinggemma:300m", 768, "gemma-v1"),
        ModelProfile("nomic-embed-text:v1.5", 768, "nomic-v1"),
    )
}


def profile_for(tag: str) -> ModelProfile:
    try:
        return PROFILES[tag]
    except KeyError as error:
        raise ValueError("Unknown local embedding model profile") from error


def embedding_identity(
    model: str, model_digest: str | None, dimension: int, version: str, text: str
) -> dict[str, object]:
    configuration = f"{model}\n{model_digest}\n{dimension}\n{version}\ntruncate=false\nl2-normalized-v1"
    return {
        "model": model,
        "model_digest": model_digest,
        "dimension": dimension,
        "formatting_version": version,
        "truncate": False,
        "normalization": "l2-normalized-v1",
        "input_hash": digest(text),
        "config_hash": digest(configuration),
    }
