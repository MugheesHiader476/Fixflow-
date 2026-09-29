from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import Document, DocumentChunk
from backend.db.session import get_session_factory
from backend.processing.okf import concept_metadata, maybe_parse_concept, parse_concept
from backend.repositories.corpus import chunk_documents
from backend.services.ingestion import ingest_source, load_documents

CONCEPT = """---
type: Playbook
title: Event loop recovery
tags: [python, asyncio]
generated:
  by: process:docs-import
  at: 2026-09-29T10:00:00Z
custom_field: retained
custom_number: 1.5
---
# Event loop recovery

Check whether a running event loop exists before scheduling a coroutine.
"""


def test_okf_concept_preserves_structure_and_unknown_fields() -> None:
    concept = parse_concept(CONCEPT, "playbooks/event-loop.md")
    assert concept.concept_id == "playbooks/event-loop"
    assert concept.frontmatter["type"] == "Playbook"
    assert concept.body.startswith("# Event loop recovery")
    metadata = concept_metadata(concept)
    okf = metadata["okf"]
    assert isinstance(okf, dict)
    assert okf["concept_id"] == "playbooks/event-loop"
    frontmatter = okf["frontmatter"]
    assert isinstance(frontmatter, dict)
    assert frontmatter["custom_field"] == "retained"
    assert frontmatter["custom_number"] == 1.5
    generated = frontmatter["generated"]
    assert isinstance(generated, dict)
    assert generated["at"] == "2026-09-29T10:00:00+00:00"
    assert metadata["title"] == "Event loop recovery"


@pytest.mark.parametrize(
    ("content", "path"),
    [
        ("# Plain Markdown", "guide.md"),
        ("---\ntitle: Legacy frontmatter\n---\n# Guide", "guide.md"),
        ("---\ntype: []\n---\n# Guide", "guide.md"),
        (CONCEPT, "../outside.md"),
        (CONCEPT, "index.md"),
        ("---\ntype: Playbook\n", "guide.md"),
        ("---\ntype: [invalid\n---\n# Guide", "guide.md"),
        ("---\ntype: Playbook\n" + "x" * 65_536 + "\n---\n", "guide.md"),
    ],
)
def test_invalid_concept_is_not_assumed_okf(content: str, path: str) -> None:
    assert maybe_parse_concept(content, path) is None
    with pytest.raises(ValueError):
        parse_concept(content, path)


def test_uploaded_okf_uses_body_for_retrieval_and_keeps_metadata(tmp_path: Path) -> None:
    file = tmp_path / "event-loop.md"
    file.write_text(CONCEPT, encoding="utf-8")
    source_id = uuid4()
    rows = list(load_documents(file, source_id, "a" * 64))
    assert len(rows) == 1
    document = rows[0]
    assert document["page_content"] == parse_concept(CONCEPT, file.name).body.strip()
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["title"] == "Event loop recovery"
    chunks = list(chunk_documents(source_id, rows, 3000, 400))
    assert len(chunks) == 1
    chunk_metadata = chunks[0]["metadata"]
    assert isinstance(chunk_metadata, dict)
    assert chunk_metadata["okf"] == metadata["okf"]
    assert "custom_field" not in str(chunks[0]["content"])


def test_plain_markdown_upload_is_unchanged(tmp_path: Path) -> None:
    file = tmp_path / "guide.md"
    file.write_text("---\ntitle: Guide\n---\n# Plain guide", encoding="utf-8")
    rows = list(load_documents(file, uuid4(), "b" * 64))
    assert rows[0]["page_content"] == "---\ntitle: Guide\n---\n# Plain guide"
    metadata = rows[0]["metadata"]
    assert isinstance(metadata, dict)
    assert "okf" not in metadata


@pytest.mark.anyio
async def test_okf_upload_persists_body_and_frontmatter(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/documents", files={"file": ("event-loop.md", CONCEPT.encode("utf-8"))})
    assert response.status_code == 202
    source_id = UUID(response.json()["source_id"])
    await ingest_source(source_id)
    assert (await client.get(f"/api/sources/{source_id}")).json()["status"] == "ready_for_embedding"
    async with get_session_factory()() as session:
        document = (await session.scalars(select(Document).where(Document.source_id == source_id))).one()
        chunk = (await session.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id))).one()
    assert document.title == "Event loop recovery"
    assert document.page_content.startswith("# Event loop recovery")
    assert chunk.meta["okf"] == document.meta["okf"]
    assert chunk.embedding is None


def test_okf_rejects_recursive_yaml_alias() -> None:
    content = "---\ntype: Reference\nloop: &loop [*loop]\n---\nbody"
    with pytest.raises(ValueError, match="cycle"):
        parse_concept(content, "recursive.md")
