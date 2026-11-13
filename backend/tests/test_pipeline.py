"""Golden evidence, stage gates, routing, provenance, cache and incremental regression tests."""

import copy
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

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
    assert result.contract_version == 3
    assert all(p.source_id == "golden" for p in result.canonical.provenance)
    assert set(result.model_dump()) == {
        "canonical",
        "engine_version",
        "config_hash",
        "stage_hashes",
        "warnings",
        "contract_version",
    }
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
    with pytest.raises(PipelineError, match="needs OCR, which is not available"):
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
    assert result.canonical.provenance[0].extraction_method == "ocr"


def test_cache_skips_inspection_and_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = run_pipeline(GOLDEN / "guide.md", "cached")

    def forbidden(*args, **kwargs):
        raise AssertionError("Cached source must not be inspected or parsed again")

    monkeypatch.setattr("backend.processing.pipeline.runner.inspect", forbidden)
    current = run_pipeline(GOLDEN / "guide.md", "cached", previous=previous)
    assert current.model_dump() == previous.model_dump()


def test_updates_preserve_version_and_ordered_content(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text((GOLDEN / "guide.md").read_text())
    previous = run_pipeline(path, "incremental")
    path.write_text(path.read_text().replace("Retry failed uploads", "Reprocess failed uploads"))
    current = run_pipeline(path, "incremental", previous=previous)
    assert current.canonical.source.version == 2
    assert current.canonical.blocks[0] == previous.canonical.blocks[0]
    assert current.stage_hashes["canonical"] != previous.stage_hashes["canonical"]


def test_force_reprocesses_and_config_change_invalidates_cache() -> None:
    previous = run_pipeline(GOLDEN / "guide.md", "force")
    forced = run_pipeline(GOLDEN / "guide.md", "force", previous=previous, force=True)
    assert forced.canonical.source.version == previous.canonical.source.version
    changed = run_pipeline(GOLDEN / "guide.md", "force", PipelineConfig(max_blocks=100), previous=previous)
    assert changed.config_hash != previous.config_hash


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


def test_process_boundary_enforces_deadline_and_output_limit() -> None:
    with pytest.raises(PipelineError, match=r"time limit|processing limit"):
        execute_pipeline(GOLDEN / "guide.md", "deadline", PipelineConfig(processing_timeout_seconds=0.001))
    with pytest.raises(PipelineError, match="artifact size"):
        execute_pipeline(GOLDEN / "guide.md", "bounded", PipelineConfig(max_result_bytes=1024))


def test_content_inspection_recognizes_structured_text_in_txt(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_bytes((GOLDEN / "data.json").read_bytes())
    result = run_pipeline(path, "sniff")
    assert result.canonical.profile.modality == "json"


def test_common_document_shapes_degrade_without_failing(tmp_path: Path) -> None:
    """Ragged tables, empty grids and unparseable code remain loadable, exact evidence with warnings."""
    cases = {
        "repl.md": "# Guide\n\nUse it like this:\n\n```python\n>>> import foo\n>>> foo.run()\n```\n",
        "ragged.html": "<html><body><h1>Pricing</h1><table><tr><th colspan='2'>Plans</th></tr>"
        "<tr><td>Basic</td><td>$5</td><td>monthly</td></tr></table><p>Notes</p></body></html>",
        "empty.html": "<html><body><p>Intro</p><table></table><p>After</p></body></html>",
        "trailing.csv": "name,qty\napple,1\nbanana,2\n\n",
        "short.csv": "name,qty,unit\napple,1\nbanana,2,kg\n",
        "legacy.py": "print 'hello'\n",
        "headers.md": "| a | b |\n|---|---|\n",
    }
    results = {}
    for name, body in cases.items():
        path = tmp_path / name
        path.write_text(body)
        results[name] = run_pipeline(path, "tolerant-" + name)
    fence = next(b for b in results["repl.md"].canonical.blocks if b.type == "code")
    assert fence.content == ">>> import foo\n>>> foo.run()\n"
    assert fence.structured_content == {"language": "python", "symbols": []}
    assert any("code fence does not parse" in warning for warning in results["repl.md"].warnings)
    table = next(b for b in results["ragged.html"].canonical.blocks if b.type == "table")
    assert table.structured_content == {
        "headers": ["Plans", "Column 2", "Column 3"],
        "rows": [["Basic", "$5", "monthly"]],
    }
    assert any("padded" in warning for warning in results["ragged.html"].warnings)
    assert [b.content for b in results["empty.html"].canonical.blocks] == ["Intro", "After"]
    assert results["trailing.csv"].canonical.blocks[0].structured_content["rows"] == [["apple", "1"], ["banana", "2"]]
    short = results["short.csv"].canonical.blocks[0].structured_content
    assert short == {"headers": ["name", "qty", "unit"], "rows": [["apple", "1", ""], ["banana", "2", "kg"]]}
    legacy = results["legacy.py"].canonical.blocks[0]
    assert legacy.type == "code" and legacy.content == "print 'hello'\n"
    assert legacy.structured_content["representation"] == "source_lexical"
    assert any("does not parse as Python 3" in warning for warning in results["legacy.py"].warnings)
    header_only = results["headers.md"].canonical.blocks[0]
    assert header_only.type == "paragraph" and header_only.content == "| a | b |\n| --- | --- |"
    for result in results.values():
        assert result.canonical.parse_quality.passed and result.contract_version == 3
