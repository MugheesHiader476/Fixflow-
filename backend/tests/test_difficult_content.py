"""Difficult sources remain authorized, bounded, reconstructable and independently validated."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.parsers import Builder, CallbackParser, source_value
from backend.processing.pipeline.runner import PipelineError, run_pipeline
from backend.processing.pipeline.units import selected_content, validate_continuations
from backend.processing.pipeline.validation import validate_result
from backend.repositories.prepared import prepared_source
from backend.schemas.pipeline import PipelineResult, digest
from backend.services.ingestion import IngestionError, ingest_source
from backend.tests.difficult_corpus import difficult_corpus

WHEN = datetime(2026, 10, 6, tzinfo=UTC)
CONTEXT: dict[str, object] = {"permissions": {"visibility": "private", "application_owner": "owner_a"}}
FIXTURES = Path(__file__).parent / "fixtures/pipeline"


@pytest.mark.parametrize(("name", "body"), difficult_corpus(), ids=[name for name, _ in difficult_corpus()])
def test_difficult_sources_are_reconstructable_and_deterministic(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / name
    path.write_text(body)
    first = run_pipeline(path, "difficult", ingested_at=WHEN, source_context=CONTEXT)
    second = run_pipeline(path, "difficult", ingested_at=WHEN, source_context=CONTEXT)
    assert first == second
    roundtrip = PipelineResult.model_validate_json(first.model_dump_json())
    validate_result(roundtrip)
    assert first.coverage["coverage_percent"] == 100
    assert first.coverage["uncovered_elements"] == []
    assert first.coverage["duplicate_content_positions"] == 0
    groups = validate_continuations(first.chunks, first.atomic_units)
    for unit in first.atomic_units:
        locations = [s for c in first.chunks for s in c.unit_slices if s.unit_id == unit.unit_id and s.continuation]
        if not locations:
            continue
        assert "".join(selected_content(unit, s)[0] for s in locations) == unit.content
        declarations = [s.continuation for s in locations if s.continuation is not None]
        assert [s.index for s in declarations] == list(range(len(locations)))
        assert all(s.full_unit_hash == digest(unit.content) for s in declarations)
    for index, chunk in enumerate(first.chunks):
        assert chunk.byte_length == len(chunk.retrieval_content.encode()) <= 1024
        assert chunk.authorization_scope == "source:difficult"
        assert chunk.source_context == first.canonical.source.context
        assert chunk.canonical_document_id == first.canonical.document_id
        assert chunk.previous_id == (first.chunks[index - 1].chunk_id if index else None)
        assert chunk.next_id == (first.chunks[index + 1].chunk_id if index + 1 < len(first.chunks) else None)
        assert all(p.source_id == "difficult" for p in chunk.provenance)
    if groups:
        assert first.coverage["continuation_reconstruction_percent"] == 100
    if name in {"function.js", "class.ts", "class.java", "function.c", "function.cpp", "function.go", "function.rs"}:
        assert any(s.symbol_paths for c in first.chunks for s in c.unit_slices)
    if name in {"nested.json", "nested.yaml"}:
        assert any(str(u.structure.get("path", "")).count("/level") == 100 for u in first.atomic_units)
    if name in {"cell.md", "row.csv", "header.csv"}:
        assert groups
        table = next(u for u in first.atomic_units if u.content_type == "table")
        assert table.structure["headers"] and table.structure["rows"]
        assert all(s.continuation.table_row is not None for c in first.chunks for s in c.unit_slices if s.continuation)
    if name in {"integer.json", "exponent.json", "precise.json", "integer.yaml", "exponent.yaml"}:
        assert "".join(c.raw_content for c in first.chunks) == body
        assert any(u.structure.get("representation") == "source_lexical" for u in first.atomic_units)


@pytest.mark.parametrize("damage", ["offset", "index", "count", "hash", "path", "group", "missing", "auth"])
def test_continuation_tampering_never_passes_final_gate(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "large.xml"
    path.write_text("<root><paragraph>" + "authorized evidence " * 900 + "</paragraph></root>")
    result = run_pipeline(path, "tamper", source_context=CONTEXT)
    chunk = result.chunks[1]
    location = next(s for s in chunk.unit_slices if s.continuation)
    continuation = location.continuation
    assert continuation
    if damage == "offset":
        assert location.character_start is not None
        location.character_start += 1
    elif damage == "index":
        continuation.index += 1
    elif damage == "count":
        continuation.count += 1
    elif damage == "hash":
        continuation.full_unit_hash = "0" * 64
    elif damage == "path":
        continuation.structural_path = "/invented"
    elif damage == "group":
        continuation.group_id = "foreign"
    elif damage == "missing":
        result.chunks.pop()
    else:
        chunk.authorization_scope = "source:foreign"
    with pytest.raises(ValueError):
        validate_result(result)


def test_repeated_parts_are_not_collapsed_and_changes_are_versioned(tmp_path: Path) -> None:
    path = tmp_path / "repeat.js"
    path.write_text("const value = '" + "x" * 18000 + "';")
    first = run_pipeline(path, "versions", source_context=CONTEXT)
    assert len({c.chunk_id for c in first.chunks}) == len(first.chunks) > 10
    assert run_pipeline(path, "versions", previous=first, source_context=CONTEXT) == first
    path.write_text(path.read_text().replace("xxx", "yyy", 1))
    second = run_pipeline(path, "versions", previous=first, source_context=CONTEXT)
    assert second.canonical.source.version == first.canonical.source.version + 1
    assert second.stage_hashes["source"] != first.stage_hashes["source"]
    assert second.coverage["continuation_reconstruction_percent"] == 100


def test_source_tree_cycles_are_rejected_without_metadata_depth_limit() -> None:
    tree: dict[str, object] = {}
    tree["self"] = tree
    with pytest.raises(ValueError, match="cycle"):
        source_value(tree)


def test_deep_json_preserves_array_vs_numeric_key_and_empty_container_identity(tmp_path: Path) -> None:
    original: object = {"0": [{"description": "evidence"}], "empty": {}, "sequence": [], "a~/b": "escaped"}
    for _ in range(60):
        original = {"level": original}
    path = tmp_path / "deep.json"
    path.write_text(json.dumps(original))
    result = run_pipeline(path, "deep-reconstruction", source_context=CONTEXT)
    rebuilt: dict[str, object] = {}
    for unit in result.atomic_units:
        if "ancestor_types" not in unit.structure:
            continue
        components = str(unit.structure["path"])[2:].split("/")
        kinds = unit.structure["ancestor_types"]
        assert isinstance(kinds, list) and len(kinds) == len(components)
        current: dict[str, object] | list[object] = rebuilt
        for index, component in enumerate(components):
            key = component.replace("~1", "/").replace("~0", "~")
            last = index + 1 == len(components)
            value = unit.structure["value"] if last else ([] if kinds[index + 1] == "array" else {})
            if isinstance(current, list):
                position = int(key)
                while len(current) <= position:
                    current.append(None)
                if current[position] is None:
                    current[position] = value
                child = current[position]
            else:
                child = current.setdefault(key, value)
            if not last:
                assert isinstance(child, (dict, list))
                current = cast(dict[str, object] | list[object], child)
    assert rebuilt == original


def test_large_xml_with_benign_doctype_keeps_all_original_syntax(tmp_path: Path) -> None:
    path = tmp_path / "doctype.xml"
    body = '<!DOCTYPE article><article version="2">' + "original evidence " * 900 + "</article>"
    path.write_text(body)
    result = run_pipeline(path, "doctype", source_context=CONTEXT)
    assert "".join(c.raw_content for c in result.chunks) == body
    assert result.coverage["continuation_reconstruction_percent"] == 100


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("constant.json", '{"large":' + "9" * 10000 + ',"invalid":NaN}'),
        ("duplicates.json", '{"large":' + "9" * 10000 + ',"large":1}'),
        ("cycle.yaml", "large: " + "9" * 10000 + "\ncycle: &a [*a]"),
        ("unsafe.yaml", "large: " + "9" * 10000 + "\nunsafe: !!python/object:builtins.dict {}"),
        ("constant.yaml", "large: " + "9" * 10000 + "\ninvalid: .nan"),
        ("duplicate.yaml", "large: " + "9" * 10000 + "\nlarge: 1"),
    ],
)
def test_numeric_fallback_does_not_weaken_structured_input_validation(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / name
    path.write_text(body)
    with pytest.raises(PipelineError):
        run_pipeline(path, "invalid-numeric", source_context=CONTEXT)


@pytest.mark.anyio
@pytest.mark.parametrize("extension", ["json", "yaml"])
async def test_huge_number_preserves_type_syntax_through_database_handoff(
    client: httpx.AsyncClient,
    extension: str,
) -> None:
    body = ('{"number":' + "9" * 10000 + "}") if extension == "json" else ("number: " + "9" * 10000)
    accepted = await client.post("/api/documents", files={"file": (f"integer.{extension}", body.encode())})
    assert accepted.status_code == 202
    identifier = UUID(accepted.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        result = await prepared_source(db, identifier, "user_test")
        assert result is not None
        assert "".join(c.raw_content for c in result.chunks) == body
        assert result.coverage["continuation_reconstruction_percent"] == 100
        assert await prepared_source(db, identifier, "user_other") is None


@pytest.mark.parametrize("name", ["scanned.pdf", "scan.png"])
def test_oversized_ocr_output_enters_same_validated_contract(tmp_path: Path, name: str) -> None:
    def output(inspection, source, config):
        builder = Builder(source, "fixture-ocr", config)
        builder.asset(f"source:{source.source_id}#page/1", "image/unknown", {"page": 1, "bbox": [0, 0, 1000, 1000]})
        builder.add(
            "paragraph",
            "OCR_START " + "café 日本語 🚀 evidence " * 750 + " OCR_END",
            location={"page": 1, "bbox": [0, 0, 1000, 1000]},
            confidence=0.95,
        )
        return builder.result

    result = run_pipeline(
        FIXTURES / name,
        "ocr-output",
        PipelineConfig(parser_priority=["fixture-ocr"]),
        parsers=[CallbackParser("fixture-ocr", 3, lambda _: True, output)],
        source_context=CONTEXT,
    )
    assert result.coverage["coverage_percent"] == result.coverage["continuation_reconstruction_percent"] == 100
    assert all(p.page == 1 and p.extraction_method == "ocr" and p.bbox for c in result.chunks for p in c.provenance)
    assert "OCR_START" in result.chunks[1].raw_content
    assert "OCR_END" in result.chunks[-1].raw_content


@pytest.mark.anyio
@pytest.mark.parametrize("extension", ["txt", "js", "py", "json", "yaml", "xml", "csv"])
async def test_difficult_source_persistence_prepared_handoff_and_retrieval(
    client: httpx.AsyncClient,
    extension: str,
) -> None:
    markers = [f"ZXQ{extension.upper()}{position}98451" for position in ("BEGIN", "MIDDLE", "END")]
    value = markers[0] + " " + "café 日本語 🚀 evidence " * 400 + markers[1] + " " + "safe detail " * 400 + markers[2]
    bodies = {
        "txt": value,
        "js": f"function evidence() {{ return '{value}'; }}",
        "py": f"def evidence():\n    return '{value}'\n",
        "json": json.dumps({"description": value}),
        "yaml": "description: " + value,
        "xml": "<article><p>" + value + "</p></article>",
        "csv": "ID,Notes\nrow1," + value,
    }
    identifier = None
    snapshots = []
    for attempt in range(2):
        accepted = await client.post(
            "/api/documents",
            files={"file": (f"trace.{extension}", bodies[extension].encode())},
            data={"update_source_id": str(identifier), "force": "true"} if attempt else {},
        )
        assert accepted.status_code == 202
        current = UUID(accepted.json()["id"])
        assert identifier is None or current == identifier
        identifier = current
        await ingest_source(identifier)
        async with get_session_factory()() as db:
            result = await prepared_source(db, identifier, "user_test")
            assert result is not None
            assert await prepared_source(db, identifier, "user_other") is None
            assert result.coverage["coverage_percent"] == result.coverage["continuation_reconstruction_percent"] == 100
            records = list(
                await db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.source_id == identifier)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            snapshots.append([(r.id, r.content_hash, r.meta["unit_slices"]) for r in records])
            assert all(r.embedding is None for r in records)
            for marker in markers:
                assert any(marker in c.raw_content for c in result.chunks)
        for marker in markers:
            found = await client.post("/api/debug", json={"error": marker})
            assert found.status_code == 200 and found.json()["sources"]
            assert marker in found.json()["sources"][0]["excerpt"]
            foreign = await client.post(
                "/api/debug", json={"error": marker}, headers={"X-FixFlow-User-Id": "user_other"}
            )
            assert foreign.status_code == 200 and foreign.json()["sources"] == []
    assert snapshots[0] == snapshots[1]
    assert identifier is not None
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source is not None
        source.status = "failed"
        await db.commit()
        assert await prepared_source(db, identifier, "user_test") is None


@pytest.mark.anyio
async def test_unvalidated_continuations_rollback_and_raw_source_survives(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = await client.post("/api/documents", files={"file": ("raw.js", b"const a='" + b"x" * 6000 + b"';")})
    identifier = UUID(accepted.json()["id"])

    def damaged(path, source_id, config, **kwargs):
        result = run_pipeline(path, source_id, config, **kwargs)
        continuation = result.chunks[1].unit_slices[0].continuation
        assert continuation is not None
        continuation.full_unit_hash = "invalid"
        return result

    monkeypatch.setattr("backend.services.ingestion.run_pipeline", damaged)
    with pytest.raises(IngestionError):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source and source.status == "failed" and source.path and Path(source.path).is_file()
        assert await prepared_source(db, identifier, "user_test") is None
        assert await db.get(IngestionArtifact, identifier) is None
        assert not list(await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == identifier)))


@pytest.mark.parametrize("name", ["difficult-layout.pdf", "rotated-layout.pdf"])
def test_uncertain_layout_is_never_released_as_embedding_ready(name: str) -> None:
    with pytest.raises(PipelineError, match="layout_uncertain; needs review"):
        run_pipeline(FIXTURES / name, "uncertain-layout", source_context=CONTEXT)


@pytest.mark.parametrize("name", ["digital.pdf", "columns.pdf", "table.pdf", "integrity-audit.pdf"])
def test_verified_pdf_layouts_keep_pages_and_complete_coverage(name: str) -> None:
    result = run_pipeline(FIXTURES / name, "verified-pdf", source_context=CONTEXT)
    assert result.canonical.parse_quality.passed
    assert result.coverage["coverage_percent"] == 100
    assert all(p.page for c in result.chunks for p in c.provenance)
    if name != "digital.pdf":
        assert {p.parser for c in result.chunks for p in c.provenance} == {"layout"}


@pytest.mark.parametrize(
    "name", ["docs/ingestion-pipeline.md", "src/lib/api.ts", "backend/processing/pipeline/config.py"]
)
def test_safe_actual_project_documents_are_fully_covered(name: str) -> None:
    result = run_pipeline(Path(name), "safe-project-document", source_context=CONTEXT)
    assert result.coverage["coverage_percent"] == 100
    assert not result.coverage["uncovered_elements"]


@pytest.mark.anyio
async def test_layout_failure_retains_original_pdf_and_safe_reason(client: httpx.AsyncClient) -> None:
    body = (FIXTURES / "difficult-layout.pdf").read_bytes()
    accepted = await client.post("/api/documents", files={"file": ("difficult.pdf", body)})
    identifier = UUID(accepted.json()["id"])
    with pytest.raises(IngestionError, match="layout_uncertain"):
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        source = await db.get(KnowledgeSource, identifier)
        assert source and source.status == "failed" and source.path
        assert Path(source.path).read_bytes() == body
        assert source.error_message and "needs review" in source.error_message
        assert await prepared_source(db, identifier, "user_test") is None
