"""Embedding-ready substrate: actual evidence, independent coverage and durable authorized handoff."""

import ast
import copy
import json
import re
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import Document, DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.chunks import link_chunks
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.runner import PipelineError, run_pipeline
from backend.processing.pipeline.units import coverage_report, selected_content
from backend.processing.pipeline.validation import validate_result
from backend.repositories.prepared import prepared_source
from backend.schemas.pipeline import PipelineResult
from backend.services.ingestion import IngestionError, ingest_source

FIXTURES = Path(__file__).parent / "fixtures/pipeline"
WHEN = datetime(2026, 10, 5, tzinfo=UTC)
MARKERS = ("ZXQBEGIN47391", "ZXQMIDDLE47391", "ZXQEND47391")


def corpus() -> list[tuple[str, str]]:
    paragraph = "Authorized evidence remains complete. "
    rows = "| Identifier | Value |\n| --- | --- |\n" + "\n".join(f"| row{i} | value{i} |" for i in range(70))
    return [
        ("short.txt", "Short authorized evidence."),
        ("headings.md", "# Guide\n\nA definition.\n\n## Usage\n\nA procedure."),
        ("long.txt", paragraph * 100),
        ("nested.md", "# Guide\n\nIntroduction.\n\n## A\n\n### B\n\nImportant exception."),
        ("list.md", "# Steps\n\n" + "\n".join(f"{i}. Preserve item {i} and its explanation." for i in range(1, 70))),
        ("sample.py", "def example(value):\n    # Keep docstrings and comments\n    return value + 1\n"),
        (
            "large.py",
            'def example():\n    """Coherent procedure."""\n'
            + "".join(f"    value_{i} = {i}  # logical step {i}\n" for i in range(100))
            + "    return value_99\n",
        ),
        ("simple.json", '{"host":"localhost","enabled":true}'),
        (
            "nested.json",
            json.dumps(
                {
                    "database": {"connection": {"host": "localhost", "port": 5432}},
                    "records": [{"index": i, "payload": "Complete evidence " * 8} for i in range(30)],
                }
            ),
        ),
        ("data.yaml", "database:\n  connection:\n    host: localhost\n    port: 5432\nflags:\n  - safe\n  - private\n"),
        ("table.md", "# Metrics\n\n| Metric | Value |\n| --- | --- |\n| Timeout | 30 |"),
        ("large-table.md", "# Metrics\n\n" + rows),
        ("repeated.md", "# Repetition\n\n" + "Repeated evidence.\n\n" * 8),
        ("unicode.txt", "Unicode café 日本語 🚀 remains complete. " * 60),
        ("tiny.md", "# A\n\nOK"),
        ("huge.md", "# Large\n\n" + "x" * 16000),
        (
            "concepts.md",
            "# Guide\n\n## Definition\n\nDefine authorization.\n\n## Procedure\n\n1. Verify owner.\n2. Read evidence.",
        ),
        ("quote.md", "# Context\n\n> This qualification applies to every operation.\n\nA complete conclusion."),
        ("empty-object.json", "{}"),
        ("mixed.xml", '<guide version="2">START<section>Middle</section>END</guide>'),
    ]


@pytest.mark.parametrize(("name", "body"), corpus(), ids=[name for name, _ in corpus()])
def test_complete_deterministic_corpus(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    context: dict[str, object] = {"permissions": {"visibility": "private", "application_owner": "owner_a"}}
    first = run_pipeline(path, "corpus", ingested_at=WHEN, source_context=context)
    second = run_pipeline(path, "corpus", ingested_at=WHEN, source_context=context)
    assert first.model_dump() == second.model_dump()
    validate_result(PipelineResult.model_validate_json(first.model_dump_json()))
    assert first.coverage["coverage_percent"] == 100
    assert first.coverage["uncovered_elements"] == []
    assert first.coverage["duplicate_content_positions"] == 0
    assert len(first.atomic_units) == len(first.canonical.blocks)
    by_unit = {u.unit_id: u for u in first.atomic_units}
    for index, chunk in enumerate(first.chunks):
        assert chunk.source_id == "corpus" and chunk.authorization_scope == "source:corpus"
        assert chunk.canonical_document_id == first.canonical.document_id
        assert chunk.order_index == index
        assert chunk.previous_id == (first.chunks[index - 1].chunk_id if index else None)
        assert chunk.next_id == (first.chunks[index + 1].chunk_id if index + 1 < len(first.chunks) else None)
        assert chunk.byte_length == len(chunk.retrieval_content.encode()) <= 1024
        assert chunk.character_length == len(chunk.retrieval_content)
        assert chunk.source_context["permissions"] == context["permissions"]
        assert all(by_unit[s.unit_id].authorization_scope == chunk.authorization_scope for s in chunk.unit_slices)
    if name == "large.py":
        assert len(first.chunks) > 1
        original = next(u for u in first.atomic_units if u.structure.get("symbol") == "example")
        spans = [
            s for c in first.chunks for s in c.unit_slices if s.unit_id == original.unit_id and s.role == "content"
        ]
        assert "".join(selected_content(original, s)[0] for s in spans) == original.content
        assert all(s.line_start and s.line_end and s.line_start <= s.line_end for s in spans)
        for chunk in first.chunks:
            ast.parse(chunk.raw_content if chunk.raw_content.startswith("def") else textwrap.dedent(chunk.raw_content))
    if name == "list.md":
        for chunk in first.chunks:
            assert re.match(r"\d+\. ", chunk.raw_content)
            assert chunk.raw_content.rstrip().endswith("explanation.")
    if name == "mixed.xml":
        assert first.chunks[0].raw_content == body


def test_json_paths_and_array_indices_reconstruct_original_leaves(tmp_path: Path) -> None:
    raw = {
        "a/b": {
            "x~y": [{"id": i, "description": "Evidence " * 35, "qualification": "Required " * 20} for i in range(8)]
        }
    }
    path = tmp_path / "paths.json"
    path.write_text(json.dumps(raw))
    result = run_pipeline(path, "paths", PipelineConfig(max_chunk_tokens=450))
    unit = next(u for u in result.atomic_units if u.content_type == "code")
    pointers = [s.json_pointer for c in result.chunks for s in c.unit_slices if s.role == "content"]
    assert any(p and "/a~1b/x~0y/7/" in p for p in pointers)
    assert all("$/a~1b/a~1b" not in " / ".join(c.section_path) for c in result.chunks)
    assert result.coverage["coverage_percent"] == 100
    selected = [
        json.loads(selected_content(unit, s)[0]) for c in result.chunks for s in c.unit_slices if s.role == "content"
    ]
    assert len(selected) == 24
    assert [item["id"] for item in selected if "id" in item] == list(range(8))


def test_nested_alias_relationships_remain_reciprocal(tmp_path: Path) -> None:
    path = tmp_path / "aliases.md"
    path.write_text(
        "# Auth\n\n## JWT\n\nJSON Web Token (JWT) authenticates sessions.\n\n"
        "## JSON Web Token\n\nJSON Web Token (JWT) authenticates sessions."
    )
    result = run_pipeline(path, "aliases")
    assert len(result.concepts) == 2 and len(result.chunks) == 2
    assert result.chunks[0].raw_content == result.chunks[1].raw_content
    assert result.chunks[0].section_path != result.chunks[1].section_path
    parent, child = result.concepts
    assert parent.child_concept_ids == [child.concept_id]
    assert child.parent_concept_id == parent.concept_id
    assert result.coverage["coverage_percent"] == 100


def test_revisited_heading_preserves_global_evidence_order(tmp_path: Path) -> None:
    path = tmp_path / "order.md"
    path.write_text("# A\n\nFIRST evidence.\n\n# B\n\nMIDDLE evidence.\n\n# A\n\nLAST evidence.")
    result = run_pipeline(path, "order")
    assert [c.raw_content for c in result.chunks] == ["FIRST evidence.", "MIDDLE evidence.", "LAST evidence."]
    assert result.coverage["uncovered_elements"] == []


@pytest.mark.parametrize("field", ["source", "context", "unit", "slice", "concept", "neighbor", "coverage", "hash"])
def test_handoff_rejects_tampered_results(field: str) -> None:
    result = run_pipeline(FIXTURES / "guide.md", "tamper")
    result = copy.deepcopy(result)
    if field == "source":
        result.canonical.source.authorization_scope = "source:another"
    elif field == "context":
        result.chunks[0].source_context = {"permissions": {"application_owner": "other"}}
    elif field == "unit":
        result.atomic_units[0].content += "invented evidence"
    elif field == "slice":
        end = result.chunks[-1].unit_slices[0].character_end
        assert end is not None
        result.chunks[-1].unit_slices[0].character_end = end - 1
    elif field == "concept":
        result.concepts[0].child_concept_ids = []
    elif field == "neighbor":
        result.chunks[0].next_id = None
    elif field == "coverage":
        result.coverage["coverage_percent"] = 99
    else:
        result.stage_hashes["chunks"] = "fake"
    with pytest.raises(ValueError):
        validate_result(result)


def test_independent_coverage_detects_missing_json_and_table_evidence(tmp_path: Path) -> None:
    for name, body in [item for item in corpus() if item[0] in {"nested.json", "large-table.md"}]:
        path = tmp_path / name
        path.write_text(body)
        result = run_pipeline(path, "independent")
        report = coverage_report(result.chunks[:-1], result.atomic_units)
        assert report["uncovered_elements"] and report["coverage_percent"] != 100


def test_unchanged_reprocessing_and_changed_source_version(tmp_path: Path) -> None:
    path = tmp_path / "version.md"
    path.write_text("# A\n\nFIRST evidence.\n\n# B\n\nLAST evidence.")
    first = run_pipeline(path, "version", ingested_at=WHEN)
    same = run_pipeline(path, "version", previous=first)
    assert same == first
    path.write_text(path.read_text().replace("LAST", "CHANGED"))
    changed = run_pipeline(path, "version", previous=first)
    assert changed.canonical.source.version == 2
    assert changed.canonical.source.sha256 != first.canonical.source.sha256
    assert changed.chunks[0].chunk_id == first.chunks[0].chunk_id
    assert changed.chunks[-1].chunk_id != first.chunks[-1].chunk_id
    assert changed.coverage["coverage_percent"] == 100


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("bad.json", "{"),
        ("bad.exe", "untrusted"),
        ("huge.json", json.dumps({"scalar": "x" * 2000})),
        ("huge.xml", "<root>" + "x" * 2000 + "</root>"),
    ],
)
def test_malformed_or_unsplittable_input_fails_explicitly(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / name
    path.write_text(body)
    with pytest.raises(PipelineError):
        run_pipeline(path, "invalid")


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["trace.md", "trace.py", "trace.csv"])
async def test_three_full_database_traces_authorization_and_sentinel_retrieval(
    client: httpx.AsyncClient, name: str
) -> None:
    if name.endswith("md"):
        body = (
            "# Guide\n\n"
            + MARKERS[0]
            + " introduction.\n\n## Details\n\n"
            + MARKERS[1]
            + " Unicode café 日本語 🚀. "
            + "Complete authorized evidence. " * 60
            + "\n\n## Exception\n\n"
            + MARKERS[2]
            + " final exception."
        )
    elif name.endswith("py"):
        body = (
            "def trace():\n    start = '"
            + MARKERS[0]
            + "'\n    middle = '"
            + MARKERS[1]
            + " café 日本語 🚀'\n"
            + "".join(f"    value_{i} = {i}\n" for i in range(100))
            + "    return '"
            + MARKERS[2]
            + "'\n"
        )
    else:
        body = (
            "Identifier,Evidence\n"
            + MARKERS[0]
            + ",introduction\n"
            + "\n".join(f"{MARKERS[1] if i == 40 else 'row' + str(i)},café 日本語 🚀 complete row" for i in range(80))
            + "\n"
            + MARKERS[2]
            + ",exception"
        )
    snapshots = []
    source_id = None
    for attempt in range(3):
        data = {"update_source_id": str(source_id), "force": "true"} if attempt else {}
        accepted = await client.post("/api/documents", files={"file": (name, body.encode())}, data=data)
        assert accepted.status_code == 202, accepted.text
        identifier = UUID(accepted.json()["id"])
        assert source_id is None or identifier == source_id
        source_id = identifier
        await ingest_source(identifier)
        async with get_session_factory()() as db:
            result = await prepared_source(db, identifier, "user_test")
            assert result is not None and result.contract_version == 2
            assert await prepared_source(db, identifier, "user_other") is None
            assert result.coverage["coverage_percent"] == 100
            source = await db.get(KnowledgeSource, identifier)
            assert source and source.status == "ready_for_embedding"
            records = list(
                await db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.source_id == identifier)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            assert all(record.embedding is None for record in records)
            snapshots.append([(record.id, record.content_hash, record.meta["unit_slices"]) for record in records])
            for marker in MARKERS:
                assert sum(marker in chunk.raw_content for chunk in result.chunks) == 1
            assert all(marker in "\n".join(u.content for u in result.atomic_units) for marker in MARKERS)
        for marker in MARKERS:
            found = await client.post("/api/debug", json={"error": marker})
            assert found.status_code == 200 and found.json()["sources"]
            assert marker in found.json()["sources"][0]["excerpt"]
            foreign = await client.post(
                "/api/debug", json={"error": marker}, headers={"X-FixFlow-User-Id": "user_other"}
            )
            assert foreign.status_code == 200 and foreign.json()["sources"] == []
    assert snapshots[0] == snapshots[1] == snapshots[2]


@pytest.mark.anyio
async def test_final_gate_rolls_back_and_does_not_mark_corruption_ready(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepted = await client.post("/api/documents", files={"file": ("safe.md", b"# Safe\n\nAuthorized evidence.")})
    identifier = UUID(accepted.json()["id"])

    def corrupted(path, source_id, config, **kwargs):
        result = run_pipeline(path, source_id, config, **kwargs)
        result.chunks[0].raw_content = "silently corrupted"
        return result

    monkeypatch.setattr("backend.services.ingestion.run_pipeline", corrupted)
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source and source.status == "failed"
        assert await db.get(IngestionArtifact, identifier) is None
        assert not list(await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == identifier)))


@pytest.mark.anyio
async def test_persisted_corruption_and_legacy_artifact_are_not_embedding_ready(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/documents", files={"file": ("safe.md", b"# Safe\n\nAuthorized evidence.")})
    identifier = UUID(response.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory().begin() as db:
        row = await db.scalar(select(DocumentChunk).where(DocumentChunk.source_id == identifier))
        assert row
        row.content += "corruption"
    async with get_session_factory()() as db:
        with pytest.raises(ValueError, match="projection mismatch"):
            await prepared_source(db, identifier, "user_test")
        assert await prepared_source(db, identifier, "user_other") is None
    async with get_session_factory().begin() as db:
        artifact = await db.get(IngestionArtifact, identifier)
        assert artifact
        artifact.result = {**artifact.result, "contract_version": 1}
    async with get_session_factory()() as db:
        with pytest.raises(ValueError, match="reprocessing"):
            await prepared_source(db, identifier, "user_test")


def test_yaml_comments_remain_searchable_and_hashes_inside_scalars_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "comments.yaml"
    path.write_text(
        "# ZXQBEGIN47391 owner policy\ndatabase:\n  host: localhost # ZXQMIDDLE47391 internal only\n"
        "  example: 'quoted # stays'\n  multiline: |\n    # scalar data stays\n"
        "# ZXQEND47391 final exception\n"
    )
    result = run_pipeline(path, "yaml-comments")
    text = "\n".join(chunk.raw_content for chunk in result.chunks)
    assert all(marker in text for marker in MARKERS)
    assert text.count("quoted # stays") == text.count("# scalar data stays") == 1
    assert sum(unit.structure.get("syntax") == "yaml_comment" for unit in result.atomic_units) == 3
    assert result.coverage["coverage_percent"] == 100


@pytest.mark.anyio
@pytest.mark.parametrize("update", [False, True])
async def test_identical_upload_upgrades_old_contract_without_changing_source_id(
    client: httpx.AsyncClient, update: bool
) -> None:
    body = b"# Old\n\nAuthorized evidence."
    response = await client.post("/api/documents", files={"file": ("old.md", body)})
    identifier = UUID(response.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source
        source.ingestion_metadata = {
            key: value for key, value in source.ingestion_metadata.items() if key != "pipeline_contract_version"
        }
        artifact = await db.get(IngestionArtifact, identifier)
        assert artifact
        artifact.result = {**artifact.result, "contract_version": 1, "config_hash": "old-engine"}
    options = {"update_source_id": str(identifier)} if update else {}
    response = await client.post("/api/documents", files={"file": ("old.md", body)}, data=options)
    assert response.status_code == 202 and response.json()["id"] == str(identifier)
    assert response.json()["status"] == "uploaded"
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        result = await prepared_source(db, identifier, "user_test")
        assert result is not None and result.contract_version == 2
        assert result.canonical.source.version == 1


def test_validated_chunk_order_cannot_be_forged_by_relinking() -> None:
    result = run_pipeline(FIXTURES / "guide.md", "order-corruption")
    result.chunks.reverse()
    result.parents = link_chunks(result.chunks)
    with pytest.raises(ValueError, match="canonical evidence order"):
        validate_result(result)


def test_large_class_preserves_method_identity_and_code_line_ranges(tmp_path: Path) -> None:
    path = tmp_path / "class.py"
    text = "class Example:\n"
    for index in range(4):
        text += f"    def method_{index}(self):\n"
        text += "".join(f"        value_{step} = {step}\n" for step in range(25))
        text += "        return value_24\n"
    path.write_text(text)
    result = run_pipeline(path, "class")
    paths = {tuple(path) for c in result.chunks for r in c.unit_slices for path in r.symbol_paths}
    assert {("Example", f"method_{i}") for i in range(4)} <= paths
    for chunk in result.chunks:
        for location in chunk.unit_slices:
            if location.role == "content":
                assert location.line_start and location.line_end
                original = "".join(text.splitlines(keepends=True)[location.line_start - 1 : location.line_end])
                assert original == chunk.raw_content
    assert result.coverage["coverage_percent"] == 100


def test_fenced_python_lines_resolve_to_original_source(tmp_path: Path) -> None:
    path = tmp_path / "fence.md"
    text = "# Example\n\n```python\ndef example():\n    return 1\n```\n"
    path.write_text(text)
    result = run_pipeline(path, "fence")
    block = next(b for b in result.canonical.blocks if b.type == "code")
    assert block.provenance.line_start == 4 and block.provenance.line_end == 5
    reference = next(r for r in result.chunks[0].unit_slices if r.role == "content")
    assert reference.symbol_paths == [["example"]]
    assert reference.line_start is not None and reference.line_end is not None
    assert (
        "".join(text.splitlines(keepends=True)[reference.line_start - 1 : reference.line_end])
        == result.chunks[0].raw_content
    )


def test_uploaded_okf_title_does_not_erase_actual_heading_context(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\ntype: Reference\ntitle: Runtime validation\ncustom: retained\n---\n"
        "# Recovery\nRuntimevalidationmarker: recover a session safely."
    )
    result = run_pipeline(path, "okf-context", strict_okf=True)
    assert len(result.concepts) == len(result.chunks) == 1
    assert result.concepts[0].title == "Runtime validation"
    assert result.chunks[0].section_path == ["Recovery"]
    assert "Section: Recovery" in result.chunks[0].retrieval_content
    assert result.coverage["coverage_percent"] == 100


@pytest.mark.anyio
async def test_parent_document_corruption_is_rejected_before_handoff(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/documents", files={"file": ("parent.md", b"# Parent\n\nCoherent evidence.")})
    identifier = UUID(response.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory().begin() as db:
        document = await db.scalar(select(Document).where(Document.source_id == identifier))
        assert document
        document.page_content += "invented parent evidence"
    async with get_session_factory()() as db:
        with pytest.raises(ValueError, match="concept projection mismatch"):
            await prepared_source(db, identifier, "user_test")
        assert await prepared_source(db, identifier, "user_other") is None


@pytest.mark.parametrize("name", ["range.yaml", "range.csv"])
def test_normalized_structures_never_invent_source_line_numbers(tmp_path: Path, name: str) -> None:
    text = "database:\n  host: localhost\n  port: 5432\n" if name.endswith("yaml") else "Identifier,Value\nA,1\nB,2\n"
    path = tmp_path / name
    path.write_text(text)
    result = run_pipeline(path, "source-lines")
    location = next(r for c in result.chunks for r in c.unit_slices if r.role == "content")
    unit = next(u for u in result.atomic_units if u.unit_id == location.unit_id)
    assert location.line_scope == "unit"
    assert location.line_start == unit.provenance.line_start == 1
    assert location.line_end == unit.provenance.line_end == len(text.splitlines())
    assert len(unit.content.splitlines()) > len(text.splitlines())


def test_fenced_code_with_trailing_blank_lines_keeps_complete_line_span(tmp_path: Path) -> None:
    path = tmp_path / "blank.md"
    text = "# Example\n\n```python\ndef example():\n    return 1\n\n\n```\n"
    path.write_text(text)
    result = run_pipeline(path, "blank-lines")
    location = next(r for r in result.chunks[0].unit_slices if r.role == "content")
    assert location.line_scope == "fragment" and location.line_start == 4 and location.line_end == 7
    assert "".join(text.splitlines(keepends=True)[3:7]) == result.chunks[0].raw_content
