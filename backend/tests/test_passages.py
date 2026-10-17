"""Passage boundaries never replace or discard the canonical evidence contract."""

import json
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from backend.processing import passages as module
from backend.processing.passages import build_passages, reconstruct, tokens, validate_passage
from backend.processing.pipeline.runner import run_pipeline
from backend.schemas.evidence import IndexRequest

FIXTURES = Path(__file__).parent / "fixtures/pipeline"
LABELS = IndexRequest(project="Atlas", title="Weekly Report", document_date=date(2026, 10, 16))


@pytest.mark.parametrize(
    "name",
    [
        "guide.md",
        "guide.docx",
        "digital.pdf",
        "message.eml",
        "sample.py",
        "table.md",
        "workbook.xlsx",
        "data.csv",
        "data.json",
        "data.yaml",
        "data.xml",
    ],
)
def test_all_supported_structures_are_bounded_and_reconstructable(name: str) -> None:
    document = run_pipeline(FIXTURES / name, "boundaries").canonical
    before = document.model_dump()
    values = build_passages(document, LABELS)
    assert values == build_passages(document, LABELS)
    for p in values:
        validate_passage(document, p)
        assert p.tokens == tokens(p.text) <= 650
        assert p.prefix.startswith("Project: Atlas\nSource: Weekly Report\nSection: ")
        assert "Date: 2026-10-16" in p.prefix
    assert document.model_dump() == before


def test_heading_runs_and_long_paragraph_sentence_overlap(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    first = "Payment testing is incomplete and the release remains blocked. " * 170
    path.write_text("# Release blockers\n\n" + first + "\n\n# Release blockers\n\nA different section has no blocker.")
    document = run_pipeline(path, "report").canonical
    values = build_passages(document, LABELS)
    assert len(values) > 3
    assert len({p.group for p in values}) == 2
    spans = [s for p in values for s in p.spans if s.block_id == document.blocks[1].block_id]
    assert spans[0].start == 0 and spans[-1].end == len(document.blocks[1].content)
    for left, right in pairwise(spans):
        assert right.start <= left.end
        assert tokens(document.blocks[1].content[right.start : left.end]) <= 40
        assert document.blocks[1].content[: left.end].rstrip().endswith(".")
    assert all(p.tokens <= 450 for p in values)


def test_short_and_long_email_keep_metadata_outside_body(tmp_path: Path) -> None:
    for size in [1, 180]:
        path = tmp_path / "mail.eml"
        path.write_text(
            "From: a@example.test\nSubject: Blockers\nDate: Fri, 16 Oct 2026 12:00:00 +0000\n"
            "Message-ID: <mail-1>\nReferences: <root>\n\n" + "Payment testing blocks release. " * size
        )
        document = run_pipeline(path, "mail").canonical
        values = build_passages(document, IndexRequest(project="Atlas"))
        assert (len(values) == 1) == (size == 1)
        assert all(p.kind == "email" and p.metadata["sender"] == "a@example.test" for p in values)
        assert all(p.metadata["thread_id"] == "<root>" for p in values)
        assert all("a@example.test" not in p.content for p in values)


def test_code_functions_remain_separate_and_large_statements_keep_lines(tmp_path: Path) -> None:
    path = tmp_path / "code.py"
    path.write_text("def small():\n    return 42\n\ndef large():\n" + "    print('payment test')\n" * 300)
    document = run_pipeline(path, "code").canonical
    values = build_passages(document, LABELS)
    small = [p for p in values if p.metadata.get("function") == "small"]
    assert len(small) == 1 and "return 42" in small[0].content
    large = [p for p in values if p.metadata.get("function") == "large"]
    assert len(large) > 1
    assert all(p.metadata["file_path"] == "code.py" for p in values)
    assert all(s.provenance.line_start and s.provenance.line_end for p in large for s in p.spans)
    assert all(p.content.rstrip().endswith(")") for p in large)


def test_tables_repeat_headers_and_preserve_each_original_row(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    path.write_text("Metric,Duration (ms),Cell\n" + "\n".join(f"job-{i},{i},B{i + 2}" for i in range(200)))
    document = run_pipeline(path, "table").canonical
    values = build_passages(document, LABELS)
    assert len(values) > 1
    positions = [s for p in values for s in p.spans]
    assert positions[0].start == 0 and positions[-1].end == 200
    assert all(a.end == b.start for a, b in pairwise(positions))
    assert all("Duration (ms)" in p.content for p in values)
    assert all(p.kind == "table" for p in values)
    for p in values:
        validate_passage(document, p)


def test_unicode_and_unsplittable_sentences_remain_exact(tmp_path: Path) -> None:
    path = tmp_path / "unicode.txt"
    path.write_text("پاکستان🙂漢字" * 800)
    document = run_pipeline(path, "unicode").canonical
    values = build_passages(document, LABELS)
    blocks = {b.block_id: b for b in document.blocks}
    for p in values:
        validate_passage(document, p)
        assert "\ufffd" not in p.content
        assert p.content == "\n\n".join(reconstruct(blocks, s) for s in p.spans)


def test_oversized_table_row_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "huge.csv"
    path.write_text("Name,Value\nMetric," + "payment testing " * 1000)
    document = run_pipeline(path, "huge").canonical
    with pytest.raises(ValueError, match="Indivisible"):
        build_passages(document, LABELS)


def test_slack_keeps_message_identity_and_excludes_serialized_transport_metadata(tmp_path: Path) -> None:
    metadata = {
        "channel_id": "C1",
        "channel_name": "releases",
        "message_ts": "100.1",
        "thread_ts": "100.0",
        "author_id": "U1",
        "created_at": "2026-10-16T12:00:00+00:00",
        "is_private": True,
        "files": [],
    }
    path = tmp_path / "message.md"
    path.write_text(
        "# releases 100.1\n\n"
        + "\n".join(f"{key}: {json.dumps(value)}" for key, value in metadata.items())
        + "\n\n"
        + "Payment testing blocks the release. " * 250
    )
    document = run_pipeline(path, "slack", source_context={"provider": "slack", "metadata": metadata}).canonical
    values = build_passages(document, IndexRequest(project="Atlas"))
    assert len(values) > 1
    assert all(p.kind == "slack" and p.tokens <= 600 for p in values)
    assert all(p.metadata["message_id"] == "100.1" and p.metadata["thread_id"] == "100.0" for p in values)
    assert all("channel_id:" not in p.content and "is_private:" not in p.content for p in values)
    for p in values:
        validate_passage(document, p)


def test_nested_functions_stay_whole_and_class_methods_remain_separate(tmp_path: Path) -> None:
    path = tmp_path / "nested.py"
    path.write_text(
        "def outer():\n    def inner():\n        return 1\n    return inner()\n\n"
        "class Example:\n    def first(self):\n        return 2\n\n    def second(self):\n        return 3\n"
    )
    document = run_pipeline(path, "nested").canonical
    values = build_passages(document, IndexRequest(project="Atlas"))
    outer = [p for p in values if p.metadata.get("function") == "outer"]
    assert len(outer) == 1 and "def inner" in outer[0].content
    assert len([p for p in values if p.metadata.get("function") == "Example.first"]) == 1
    assert len([p for p in values if p.metadata.get("function") == "Example.second"]) == 1


@pytest.mark.parametrize("extension", ["txt", "py"])
def test_tokenizer_work_is_bounded_for_large_indivisible_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extension: str
) -> None:
    path = tmp_path / ("bounded." + extension)
    body = "a" * 100_000
    path.write_text("value = '" + body + "'\n" if extension == "py" else body)
    document = run_pipeline(path, "bounded-work").canonical
    original = module.tokens

    def bounded(text: str) -> int:
        assert len(text) <= 22000, "Never tokenize an unbounded original unit in one call"
        return original(text)

    monkeypatch.setattr(module, "tokens", bounded)
    values = build_passages(document, LABELS)
    assert len(values) > 2
    for p in values:
        validate_passage(document, p)
