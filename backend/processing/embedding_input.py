"""Versioned text-only retrieval space, independent of canonical parsing."""

from hashlib import sha256

MODEL = "gemini-embedding-2"
DIMENSIONS = 768
INPUT_FORMAT_VERSION = "search-v1"
# The provider exposes a stable model identifier, not an immutable weights digest.
REVISION = MODEL
PROFILE = f"{MODEL}:{REVISION}:768:{INPUT_FORMAT_VERSION}"
BATCH_SIZE = 16


def profile(revision: str = REVISION) -> str:
    return f"{MODEL}:{revision}:{DIMENSIONS}:{INPUT_FORMAT_VERSION}"


def document_input(prefix: str, content: str) -> str:
    # The source title is already in the bounded prefix. Include section/date too;
    # changing labels must not reuse an embedding of different retrieval context.
    return f"title: none | text: {prefix}\n\n{content}"


def input_hash(prefix: str, content: str) -> str:
    return sha256(document_input(prefix, content).encode()).hexdigest()
