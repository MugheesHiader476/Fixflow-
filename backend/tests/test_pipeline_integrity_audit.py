"""Regressions for reproduced parser, chunk coverage, boundary and retrieval defects."""

import re
from email.message import EmailMessage
from pathlib import Path

from pypdf import PdfReader

from backend.processing.pipeline.runner import run_pipeline

SENTINELS = ("FIXFLOW_START_001", "FIXFLOW_MIDDLE_002", "FIXFLOW_END_003")
FIXTURES = Path(__file__).parent / "fixtures/pipeline"


def test_xml_mixed_content_keeps_root_text_and_child_tail(tmp_path: Path) -> None:
    path = tmp_path / "mixed.xml"
    path.write_text(
        f"<guide>{SENTINELS[0]} essential introduction."
        f"<section>{SENTINELS[1]} essential middle content.</section>"
        f"{SENTINELS[2]} essential final exception.</guide>",
        encoding="utf-8",
    )
    result = run_pipeline(path, "xml-audit")
    text = "\n".join(block.content for block in result.canonical.blocks)
    assert all(marker in text for marker in SENTINELS), text
    assert [text.index(marker) for marker in SENTINELS] == sorted(text.index(marker) for marker in SENTINELS)


def test_html_keeps_meaningful_div_and_footer_text(tmp_path: Path) -> None:
    path = tmp_path / "mixed.html"
    path.write_text(
        f"<html><body><h1>Audit</h1><div>{SENTINELS[0]} essential introduction.</div>"
        f"<p>{SENTINELS[1]} essential middle content.</p>"
        f"<footer>{SENTINELS[2]} essential final exception.</footer></body></html>",
        encoding="utf-8",
    )
    result = run_pipeline(path, "html-audit")
    text = "\n".join(block.content for block in result.canonical.blocks)
    assert all(marker in text for marker in SENTINELS), text
    assert [text.index(marker) for marker in SENTINELS] == sorted(text.index(marker) for marker in SENTINELS)


def test_html_nested_loose_text_is_ordered_once_and_hidden_code_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "nested.html"
    expected = ["START café 日本語 🚀", "MIDDLE meaningful paragraph", "TAIL text", "END final exception"]
    path.write_text(
        "<!doctype html><html><body><div>START café <strong>日本語 🚀</strong>"
        "<p>MIDDLE meaningful paragraph</p>TAIL text</div><footer>END final exception</footer>"
        "<!-- hidden comment --><script>private_script()</script><style>private_style</style></body></html>",
        encoding="utf-8",
    )
    result = run_pipeline(path, "nested-html")
    text = "\n".join(b.content for b in result.canonical.blocks)
    assert all(text.count(part) == 1 for part in expected)
    assert [text.index(part) for part in expected] == sorted(text.index(part) for part in expected)
    assert "hidden comment" not in text and "private_script" not in text and "private_style" not in text
    assert all(p.source_id == "nested-html" for p in result.canonical.provenance)


def test_pdf_soft_wraps_keep_sentences_and_code_in_complete_chunks() -> None:
    path = FIXTURES / "integrity-audit.pdf"
    result = run_pipeline(path, "pdf-audit")

    def normalize(text: str) -> str:
        return re.sub(r"\s+", "", text)

    pages = PdfReader(path).pages
    extracted = "\n".join(page.extract_text() for page in pages)
    canonical = "\n".join(b.content for b in result.canonical.blocks)
    assert normalize(extracted) == normalize(canonical)
    for index in range(35):
        sentence = (
            f"Record {index:03} states that authorized audit records retain complete evidence and exception details."
        )
        assert any(normalize(sentence) in normalize(c.content) for c in result.canonical.blocks), sentence
    assert any("def safe_audit_example():" in c.content and "return" in c.content for c in result.canonical.blocks)
    assert {b.provenance.page for b in result.canonical.blocks} == set(range(1, len(pages) + 1))
    for block in result.canonical.blocks:
        assert block.provenance.bbox
        left, top, right, bottom = block.provenance.bbox
        boxes = block.structured_content["line_bboxes"]
        assert isinstance(boxes, list) and boxes
        assert all(left <= box[0] <= box[2] <= right and top <= box[1] <= box[3] <= bottom for box in boxes)
    assert all(p.source_id == "pdf-audit" for p in result.canonical.provenance)


def test_email_trailing_newline_does_not_fragment_complete_final_sentence(tmp_path: Path) -> None:
    final = "FIXFLOW_END_003 Final exception: never expose another owner\u2019s data."
    body = "Authorized audit records retain complete evidence and exception details. " * 35 + "\n\n" + final
    message = EmailMessage()
    message["Subject"] = "Email audit"
    message.set_content(body)
    path = tmp_path / "trailing-newline.eml"
    path.write_bytes(message.as_bytes())
    result = run_pipeline(path, "email-tail-audit")
    assert any(final in chunk.content for chunk in result.canonical.blocks)
    assert re.sub(r"\s+", "", body) == re.sub(
        r"\s+", "", "".join(c.content for c in result.canonical.blocks if c.type != "heading")
    )
