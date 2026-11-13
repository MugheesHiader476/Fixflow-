"""Versioned text-only retrieval space, independent of canonical parsing."""

from hashlib import sha256

# Free local model served by Ollama. nomic-embed-text expects task prefixes on every input.
MODEL = "nomic-embed-text"
DIMENSIONS = 768
INPUT_FORMAT_VERSION = "nomic-search-v1"
# Ollama weights digest prefix (`ollama show`/`/api/tags`); a different digest is a different space.
REVISION = "0a109f422b47"
PROFILE = f"{MODEL}:{REVISION}:768:{INPUT_FORMAT_VERSION}"
BATCH_SIZE = 16


def profile(revision: str = REVISION) -> str:
    return f"{MODEL}:{revision}:{DIMENSIONS}:{INPUT_FORMAT_VERSION}"


def document_input(prefix: str, content: str) -> str:
    # The source title is already in the bounded prefix. Include section/date too;
    # changing labels must not reuse an embedding of different retrieval context.
    return f"search_document: {prefix}\n\n{content}"


def query_input(question: str) -> str:
    return "search_query: " + question


def input_hash(prefix: str, content: str) -> str:
    return sha256(document_input(prefix, content).encode()).hexdigest()
