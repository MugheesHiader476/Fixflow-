"""Difficult sources retain their complete parsed evidence with no post-parsing stages."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from backend.db.models import DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.parsers import Builder, CallbackParser, source_value
from backend.processing.pipeline.runner import PipelineError, run_pipeline
from backend.processing.pipeline.validation import validate_result
from backend.repositories.parsed import parsed_source
from backend.schemas.pipeline import PipelineResult
from backend.services.ingestion import IngestionError, ingest_source
from backend.tests.difficult_corpus import difficult_corpus

WHEN = datetime(2026, 10, 6, tzinfo=UTC)
CONTEXT: dict[str, object] = {"permissions": {"visibility": "private", "application_owner": "owner_a"}}
FIXTURES = Path(__file__).parent / "fixtures/pipeline"


@pytest.mark.parametrize(("name", "body"), difficult_corpus(), ids=[name for name, _ in difficult_corpus()])
def test_difficult_sources_parse_deterministically_without_fragments(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / name
    path.write_text(body)
    first = run_pipeline(path, "difficult", ingested_at=WHEN, source_context=CONTEXT)
    assert run_pipeline(path, "difficult", ingested_at=WHEN, source_context=CONTEXT) == first
    validate_result(PipelineResult.model_validate_json(first.model_dump_json()))
    assert first.canonical.parse_quality.passed
    assert all(p.source_id == "difficult" for p in first.canonical.provenance)
    assert not {"chunks", "concepts", "atomic_units", "bundle"}.intersection(first.model_dump())
    if name in {"integer.json", "exponent.json", "precise.json", "integer.yaml", "exponent.yaml"}:
        blocks = first.canonical.blocks
        assert len(blocks) == 1 and blocks[0].content == body
        assert blocks[0].structured_content["representation"] == "source_lexical"
    if name in {"nested.json", "nested.yaml"}:
        assert any(str(b.structured_content.get("path", "")).count("/level") == 100 for b in first.canonical.blocks)


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
    result = run_pipeline(path, "deep", source_context=CONTEXT)
    rebuilt: dict[str, object] = {}
    for block in result.canonical.blocks:
        structure = block.structured_content
        if "ancestor_types" not in structure:
            continue
        components = str(structure["path"])[2:].split("/")
        kinds = structure["ancestor_types"]
        assert isinstance(kinds, list) and len(kinds) == len(components)
        current: dict[str, object] | list[object] = rebuilt
        for index, component in enumerate(components):
            key = component.replace("~1", "/").replace("~0", "~")
            last = index + 1 == len(components)
            value = structure["value"] if last else ([] if kinds[index + 1] == "array" else {})
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


@pytest.mark.parametrize(
    "name,body",
    [
        ("constant.json", '{"large":' + "9" * 10000 + ',"invalid":NaN}'),
        ("duplicates.json", '{"large":' + "9" * 10000 + ',"large":1}'),
        ("cycle.yaml", "large: " + "9" * 10000 + "\ncycle: &a [*a]"),
        ("unsafe.yaml", "large: " + "9" * 10000 + "\nunsafe: !!python/object:builtins.dict {}"),
        ("constant.yaml", "large: " + "9" * 10000 + "\ninvalid: .nan"),
        ("duplicate.yaml", "large: " + "9" * 10000 + "\nlarge: 1"),
    ],
)
def test_numeric_fallback_keeps_input_validation(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / name
    path.write_text(body)
    with pytest.raises(PipelineError):
        run_pipeline(path, "invalid-numeric", source_context=CONTEXT)


@pytest.mark.anyio
@pytest.mark.parametrize("extension", ["txt", "js", "py", "json", "yaml", "xml", "csv"])
async def test_large_source_persists_without_chunking(client: httpx.AsyncClient, extension: str) -> None:
    markers = [f"ZXQ{position}98451" for position in ("BEGIN", "MIDDLE", "END")]
    value = markers[0] + " café 日本語 🚀 evidence " * 400 + markers[1] + " safe detail " * 400 + markers[2]
    body = {
        "txt": value,
        "js": f"function evidence() {{ return '{value}'; }}",
        "py": f"def evidence():\n    return '{value}'\n",
        "json": json.dumps({"description": value}),
        "yaml": "description: " + value,
        "xml": "<article><p>" + value + "</p></article>",
        "csv": "ID,Notes\nrow1," + value,
    }[extension]
    accepted = await client.post("/api/documents", files={"file": (f"trace.{extension}", body.encode())})
    assert accepted.status_code == 202
    identifier = UUID(accepted.json()["id"])
    await ingest_source(identifier)
    async with get_session_factory()() as db:
        result = await parsed_source(db, identifier, "user_test")
        assert result is not None
        assert await parsed_source(db, identifier, "user_other") is None
        text = "\n".join(b.content for b in result.canonical.blocks)
        assert all(marker in text for marker in markers)
        assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        source = await db.get(KnowledgeSource, identifier)
        assert source and source.status == "parsed"
        source.is_active = False
        await db.commit()
        assert await parsed_source(db, identifier, "user_test") is None


@pytest.mark.parametrize("name", ["scanned.pdf", "scan.png"])
def test_large_ocr_adapter_output_stays_whole(name: str) -> None:
    def output(inspection, source, config):
        builder = Builder(source, "fixture-ocr", config)
        builder.asset(f"source:{source.source_id}#page/1", "image/unknown", {"page": 1})
        builder.add(
            "paragraph",
            "OCR_START " + "café 日本語 🚀 evidence " * 750 + " OCR_END",
            location={"page": 1},
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
    paragraph = next(b for b in result.canonical.blocks if b.type == "paragraph")
    assert paragraph.content.startswith("OCR_START") and paragraph.content.endswith("OCR_END")
    assert paragraph.provenance.page == 1 and paragraph.provenance.extraction_method == "ocr"


@pytest.mark.parametrize("name", ["difficult-layout.pdf", "rotated-layout.pdf"])
def test_uncertain_layout_is_never_released(name: str) -> None:
    with pytest.raises(PipelineError, match="layout_uncertain; needs review"):
        run_pipeline(FIXTURES / name, "uncertain-layout", source_context=CONTEXT)


@pytest.mark.parametrize("name", ["digital.pdf", "columns.pdf", "table.pdf", "integrity-audit.pdf"])
def test_verified_pdf_layouts_keep_locations(name: str) -> None:
    result = run_pipeline(FIXTURES / name, "verified-pdf", source_context=CONTEXT)
    assert result.canonical.parse_quality.passed
    assert all(p.page for p in result.canonical.provenance)


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
        assert await db.get(IngestionArtifact, identifier) is None
