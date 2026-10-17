from uuid import UUID

import httpx
import pytest

from backend.db.session import get_session_factory
from backend.processing.okf import concept_metadata, maybe_parse_concept, parse_concept
from backend.repositories.parsed import parsed_source
from backend.services.ingestion import ingest_source

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


@pytest.mark.anyio
async def test_okf_upload_persists_body_and_frontmatter(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/documents", data={"content": CONCEPT, "ingestion_format": "okf"})
    assert response.status_code == 202
    source_id = UUID(response.json()["id"])
    await ingest_source(source_id)
    async with get_session_factory()() as session:
        result = await parsed_source(session, source_id, "user_test")
        assert result is not None
        metadata = result.canonical.metadata["uploaded_okf"]
        assert isinstance(metadata, dict) and metadata["custom_field"] == "retained"
        assert all("custom_field" not in block.content for block in result.canonical.blocks)


def test_okf_rejects_recursive_yaml_alias() -> None:
    content = "---\ntype: Reference\nloop: &loop [*loop]\n---\nbody"
    with pytest.raises(ValueError, match="cycle"):
        parse_concept(content, "recursive.md")


def test_okf_rejects_null_bytes_decoded_from_yaml() -> None:
    with pytest.raises(ValueError, match="null bytes"):
        parse_concept('---\ntype: Reference\ntitle: "bad\\u0000title"\n---\nbody', "invalid.md")
