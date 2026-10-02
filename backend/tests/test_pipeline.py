"""Golden evidence, stage gates, routing, provenance, cache and incremental regression tests."""

import copy
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.processing.pipeline.chunks import ByteTokenizer, TiktokenTokenizer, validate_chunks
from backend.processing.pipeline.concepts import validate_okf
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.execution import execute_pipeline
from backend.processing.pipeline.inspection import inspect
from backend.processing.pipeline.parsers import Builder, CallbackParser, NativeParser, OcrParser
from backend.processing.pipeline.runner import PipelineError, run_pipeline
from backend.schemas.pipeline import CanonicalDocument, Source

GOLDEN = Path(__file__).parent / "fixtures/pipeline"


@pytest.mark.parametrize(
    ("name", "modality", "required"),
    [
        ("digital.pdf", "pdf", "paragraph"),
        ("columns.pdf", "pdf", "paragraph"),
        ("table.pdf", "pdf", "table"),
        ("guide.docx", "docx", "table"),
        ("slides.pptx", "pptx", "paragraph"),
        ("data.csv", "csv", "table"),
        ("workbook.xlsx", "xlsx", "table"),
        ("data.json", "json", "code"),
        ("data.xml", "xml", "code"),
        ("data.yaml", "yaml", "code"),
        ("page.html", "html", "table"),
        ("guide.md", "markdown", "heading"),
        ("sample.py", "code", "code"),
        ("table.md", "markdown", "table"),
        ("events.log", "log", "paragraph"),
        ("message.eml", "email", "paragraph"),
        ("speech.vtt", "transcript", "paragraph"),
    ],
)
def test_golden_modalities(name: str, modality: str, required: str) -> None:
    result = run_pipeline(GOLDEN / name, "golden", ingested_at=datetime(2026, 10, 2, tzinfo=UTC))
    assert result.canonical.profile.modality == modality
    assert required in {b.type for b in result.canonical.blocks}
    assert result.canonical.parse_quality.passed
    assert result.concepts and result.chunks
    assert all(p.source_id == "golden" for c in result.chunks for p in c.provenance)
    assert all(c.token_count <= 1024 for c in result.chunks)
    assert all("verified:" not in c.markdown for c in result.concepts)
    assert result.bundle["index.md"].startswith("---\nokf_version: '0.2'")
    assert result.stage_hashes["source"] == result.canonical.source.sha256


def test_pdf_columns_are_read_in_column_order() -> None:
    result = run_pipeline(GOLDEN / "columns.pdf", "columns")
    assert [b.content for b in result.canonical.blocks] == [
        "Left column first paragraph.",
        "Left column second paragraph.",
        "Right column first paragraph.",
        "Right column second paragraph.",
    ]
    assert all(b.provenance.bbox for b in result.canonical.blocks)
    assert {b.provenance.parser for b in result.canonical.blocks} == {"layout"}


def test_pdf_and_office_tables_keep_cells_and_locations() -> None:
    for name in ("table.pdf", "guide.docx", "workbook.xlsx"):
        result = run_pipeline(GOLDEN / name, "tables")
        table = next(b for b in result.canonical.blocks if b.type == "table")
        assert table.structured_content == {"headers": ["Name", "Value"], "rows": [["Timeout", "30"]]}
        if name.endswith("xlsx"):
            assert table.provenance.sheet == "Metrics"
            assert table.provenance.cell_range


def test_structure_provenance_and_neighbor_graphs() -> None:
    result = run_pipeline(GOLDEN / "guide.md", "structure", PipelineConfig(max_chunk_tokens=110, min_chunk_tokens=8))
    assert len(result.concepts) == 2
    assert {tuple(s.path) for s in result.canonical.sections} >= {("Authentication",), ("Authentication", "Timeouts")}
    assert result.chunks[0].raw_content.startswith("Validate tokens")
    assert result.chunks[0].retrieval_content.startswith("Concept: Authentication")
    assert any(c.next_id for c in result.chunks)
    for chunk in result.chunks:
        parent = next(p for p in result.parents if p.parent_id == chunk.parent_id)
        assert chunk.chunk_id in parent.child_ids
        assert chunk.source_block_ids


def test_large_table_has_repeated_headers_and_complete_rows() -> None:
    config = PipelineConfig(max_chunk_tokens=220, min_chunk_tokens=8, table_rows_per_chunk=3)
    result = run_pipeline(GOLDEN / "table.md", "table", config)
    assert len(result.chunks) > 1
    assert all(c.raw_content.startswith("| Metric | Value |\n| --- | --- |") for c in result.chunks)
    assert sum(c.raw_content.count("requests_") for c in result.chunks) == 40
    assert all(c.token_count <= 220 for c in result.chunks)


def test_small_concept_stays_whole_and_no_overlap(tmp_path: Path) -> None:
    path = tmp_path / "small.md"
    path.write_text("# Authentication\n\nValidate tokens before opening a session.\n\nThe timeout is thirty seconds.")
    result = run_pipeline(path, "whole")
    assert len(result.chunks) == len(result.concepts) == 1
    assert result.chunks[0].raw_content.count("thirty seconds") == 1


def test_equal_text_in_different_subsections_keeps_each_context(tmp_path: Path) -> None:
    path = tmp_path / "sections.md"
    path.write_text(
        "# Requests\n\n## Read\n\nValidate authorization before processing.\n\n"
        "## Write\n\nValidate authorization before processing."
    )
    result = run_pipeline(path, "sections")
    assert len(result.concepts) == 1 and len(result.chunks) == 2
    assert [c.section_path for c in result.chunks] == [["Requests", "Read"], ["Requests", "Write"]]
    assert result.chunks[0].chunk_id != result.chunks[1].chunk_id


def test_ocr_decoder_failure_escalates_to_configured_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("decoder", 1)

    def advanced(inspection, source, config):
        builder = Builder(source, "advanced", config)
        builder.asset(f"source:{source.source_id}#page/1", "image/unknown", {"page": 1})
        builder.add("paragraph", "Validate tokens before opening a session.", location={"page": 1})
        return builder.result

    monkeypatch.setattr("backend.processing.pipeline.parsers.shutil.which", lambda name: "/trusted/" + name)
    monkeypatch.setattr("backend.processing.pipeline.parsers.subprocess.run", timeout)
    result = run_pipeline(
        GOLDEN / "scanned.pdf",
        "decoder-fallback",
        PipelineConfig(ocr_enabled=True, parser_priority=["ocr", "advanced"]),
        parsers=[OcrParser(), CallbackParser("advanced", 3, lambda _: True, advanced)],
    )
    assert result.canonical.blocks[0].provenance.parser == "advanced"


def test_expensive_fallback_is_not_called_for_good_native_output() -> None:
    def expensive(inspection, source, config):
        raise AssertionError("Native quality passed; fallback must not run")

    fallback = CallbackParser("advanced", 3, lambda _: True, expensive)
    run_pipeline(
        GOLDEN / "guide.md",
        "cheap",
        PipelineConfig(parser_priority=["native", "advanced"]),
        parsers=[fallback, NativeParser()],
    )


def test_bad_parser_output_escalates_before_canonical_generation() -> None:
    calls = []

    def bad(inspection, source, config):
        calls.append("bad")
        builder = Builder(source, "bad", config)
        builder.add("paragraph", "\ufffd" * 40)
        return builder.result

    def good(inspection, source, config):
        calls.append("good")
        return NativeParser().parse(inspection, source, config)

    config = PipelineConfig(parser_priority=["bad", "good"])
    result = run_pipeline(
        GOLDEN / "guide.md",
        "fallback",
        config,
        parsers=[CallbackParser("bad", 1, lambda _: True, bad), CallbackParser("good", 2, lambda _: True, good)],
    )
    assert calls == ["bad", "good"]
    assert result.canonical.parse_quality.passed


def test_scan_requires_real_configured_adapter_and_fallback_preserves_provenance() -> None:
    profile = inspect(GOLDEN / "scanned.pdf", PipelineConfig()).profile
    assert profile.scanned_pages == [1] and profile.text_layer is False
    with pytest.raises(PipelineError, match="parsing failed"):
        run_pipeline(GOLDEN / "scanned.pdf", "scan")

    def test_adapter(inspection, source, config):
        builder = Builder(source, "test-ocr", config)
        builder.asset(f"source:{source.source_id}#page/1", "image/unknown", {"page": 1})
        builder.add("paragraph", "Validate tokens before opening a session.", location={"page": 1}, confidence=0.99)
        return builder.result

    result = run_pipeline(
        GOLDEN / "scanned.pdf",
        "scan",
        PipelineConfig(parser_priority=["native", "test-ocr"]),
        parsers=[NativeParser(), CallbackParser("test-ocr", 3, lambda _: True, test_adapter)],
    )
    assert result.chunks[0].provenance[0].extraction_method == "ocr"


def test_cache_skips_inspection_and_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = run_pipeline(GOLDEN / "guide.md", "cached")

    def forbidden(*args, **kwargs):
        raise AssertionError("Cached source must not be inspected or parsed again")

    monkeypatch.setattr("backend.processing.pipeline.runner.inspect", forbidden)
    current = run_pipeline(GOLDEN / "guide.md", "cached", previous=previous)
    assert current.model_dump() == previous.model_dump()


def test_incremental_update_reuses_unchanged_concept_and_chunks(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text((GOLDEN / "guide.md").read_text())
    previous = run_pipeline(path, "incremental")
    path.write_text(path.read_text().replace("Retry failed uploads", "Reprocess failed uploads"))
    current = run_pipeline(path, "incremental", previous=previous)
    assert current.reused_concepts == 1
    assert current.canonical.source.version == 2
    assert current.concepts[0] == previous.concepts[0]
    assert current.chunks[0] == previous.chunks[0]
    assert current.chunks[-1].content_hash != previous.chunks[-1].content_hash
    assert current.stage_hashes["canonical"] != previous.stage_hashes["canonical"]


def test_force_reprocesses_and_config_change_invalidates_cache() -> None:
    previous = run_pipeline(GOLDEN / "guide.md", "force")
    forced = run_pipeline(GOLDEN / "guide.md", "force", previous=previous, force=True)
    assert forced.reused_concepts == 0
    changed = run_pipeline(GOLDEN / "guide.md", "force", PipelineConfig(max_chunk_tokens=130), previous=previous)
    assert changed.config_hash != previous.config_hash
    assert all(c.token_count <= 130 for c in changed.chunks)


@pytest.mark.parametrize("corruption", ["duplicate", "parent", "reference", "provenance", "table", "hash"])
def test_canonical_gate_rejects_corrupt_evidence(corruption: str) -> None:
    result = run_pipeline(GOLDEN / "guide.docx", "canonical")
    payload = result.canonical.model_dump(mode="json")
    if corruption == "duplicate":
        payload["blocks"].append(copy.deepcopy(payload["blocks"][0]))
    elif corruption == "parent":
        payload["sections"][-1]["parent_id"] = payload["sections"][-1]["section_id"]
    elif corruption == "reference":
        payload["relationships"][0]["target_id"] = "missing"
    elif corruption == "provenance":
        payload["blocks"][0]["provenance"]["source_id"] = "other"
    elif corruption == "table":
        payload["blocks"][-1]["structured_content"]["headers"] = []
    else:
        payload["blocks"][0]["content_hash"] = "wrong"
    with pytest.raises(ValidationError):
        CanonicalDocument.model_validate(payload)


def test_generated_okf_hard_errors_and_broken_links_are_warnings() -> None:
    result = run_pipeline(GOLDEN / "guide.md", "okf")
    concept = result.concepts[0].model_copy(deep=True)
    concept.markdown += "\nSee [other](/concepts/missing.md).\n"
    assert validate_okf([concept, result.concepts[1]], result.canonical) == ["Unresolved internal source link"]
    concept.markdown = concept.markdown.replace("status: draft", "status: invented")
    with pytest.raises(ValueError, match="lifecycle"):
        validate_okf([concept, result.concepts[1]], result.canonical)
    concept.markdown = result.concepts[0].markdown.replace("status: draft", "verified: {by: 'human:invented'}")
    with pytest.raises(ValueError, match="verification"):
        validate_okf([concept, result.concepts[1]], result.canonical)


def test_chunk_gate_checks_tokens_neighbors_and_provenance() -> None:
    result = run_pipeline(GOLDEN / "guide.md", "chunk-gates")
    for field, value in (("token_count", 999999), ("provenance", []), ("next_id", "missing")):
        chunks = [c.model_copy(deep=True) for c in result.chunks]
        setattr(chunks[0], field, value)
        with pytest.raises(ValueError):
            validate_chunks(
                chunks, result.parents, result.concepts, result.canonical, ByteTokenizer(), PipelineConfig()
            )


def test_heading_only_and_oversized_atomic_code_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("# Heading only")
    with pytest.raises(PipelineError):
        run_pipeline(path, "empty")
    path = tmp_path / "huge.py"
    path.write_text("def long_function():\n" + "    print('keep this function intact')\n" * 100)
    with pytest.raises(PipelineError):
        run_pipeline(path, "huge", PipelineConfig(max_chunk_tokens=100))


def test_json_structural_splitting_produces_valid_subtrees(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    values = [{"name": f"request-{i}", "value": i} for i in range(40)]
    path.write_text(json.dumps({"requests": values}))
    result = run_pipeline(path, "json", PipelineConfig(max_chunk_tokens=180, min_chunk_tokens=8))
    reconstructed = [item for chunk in result.chunks for item in json.loads(chunk.raw_content)]
    assert reconstructed == values
    assert all(c.token_count <= 180 for c in result.chunks)


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("fake.pdf", b"not a pdf"),
        ("fake.docx", b"not a zip"),
        ("fake.png", b"not an image"),
        ("data.xml", b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'),
        ("data.yaml", b"cyclic: &node [*node]"),
        ("data.json", b'{"value":NaN}'),
        ("data.json", b'{"value":1,"value":2}'),
        ("data.yaml", b"value: 1\nvalue: 2"),
    ],
)
def test_corrupt_or_unsafe_sources_fail_safely(tmp_path: Path, name: str, body: bytes) -> None:
    path = tmp_path / name
    path.write_bytes(body)
    with pytest.raises(PipelineError) as error:
        run_pipeline(path, str(uuid4()))
    assert "/etc/passwd" not in str(error.value)


def test_source_registration_has_content_hash_and_timezone() -> None:
    result = run_pipeline(GOLDEN / "guide.md", "source")
    source: Source = result.canonical.source
    assert source.size == (GOLDEN / "guide.md").stat().st_size
    assert source.filename == "guide.md" and source.extension == ".md"
    assert source.ingested_at.utcoffset() is not None


@pytest.mark.parametrize("name", ["scanned.pdf", "scan.png"])
def test_real_ocr_adapter_when_installed(name: str) -> None:
    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        pytest.skip("Install Tesseract and Poppler to exercise the real OCR adapter")
    result = run_pipeline(GOLDEN / name, "real-ocr", PipelineConfig(ocr_enabled=True))
    assert "Validate tokens before opening a session." in " ".join(b.content for b in result.canonical.blocks)
    confidence = result.canonical.parse_quality.metrics["ocr_confidence"]
    assert confidence is not None and confidence >= 0.9
    assert {b.provenance.parser for b in result.canonical.blocks} == {"ocr"}


def test_concept_alias_resolution_requires_matching_evidence(tmp_path: Path) -> None:
    path = tmp_path / "aliases.md"
    path.write_text(
        "# JWT\n\nJSON Web Token (JWT) authenticates sessions.\n\n"
        "# JSON Web Token\n\nJSON Web Token (JWT) authenticates sessions.\n"
    )
    result = run_pipeline(path, "aliases")
    assert len(result.concepts) == 1
    assert "JWT" in result.concepts[0].aliases
    assert len(result.chunks[0].source_block_ids) == 2
    assert result.chunks[0].raw_content.count("authenticates sessions") == 1
    path.write_text(path.read_text().replace("JWT) authenticates sessions.", "JWT) has different evidence.", 1))
    result = run_pipeline(path, "aliases")
    assert len(result.concepts) == 2


def test_process_boundary_enforces_deadline_and_output_limit() -> None:
    with pytest.raises(PipelineError, match=r"time limit|processing limit"):
        execute_pipeline(GOLDEN / "guide.md", "deadline", PipelineConfig(processing_timeout_seconds=0.001))
    with pytest.raises(PipelineError, match="artifact size"):
        execute_pipeline(GOLDEN / "guide.md", "bounded", PipelineConfig(max_result_bytes=1024))


def test_tokenizer_adapter_counts_enriched_text_without_a_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class Encoding:
        def encode(self, text: str, *, disallowed_special: tuple[()]) -> list[str]:
            return text.split()

    monkeypatch.setattr("tiktoken.get_encoding", lambda _: Encoding())
    tokenizer = TiktokenTokenizer("test-only-encoding")
    result = run_pipeline(GOLDEN / "guide.md", "tokens", PipelineConfig(min_chunk_tokens=2), tokenizer=tokenizer)
    assert result.canonical.metadata["tokenizer"] == "tiktoken:test-only-encoding"
    assert all(c.token_count == len(c.retrieval_content.split()) for c in result.chunks)


def test_content_inspection_recognizes_structured_text_in_txt(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_bytes((GOLDEN / "data.json").read_bytes())
    result = run_pipeline(path, "sniff")
    assert result.canonical.profile.modality == "json"
