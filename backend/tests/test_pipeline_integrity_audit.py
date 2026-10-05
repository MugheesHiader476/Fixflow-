"""Regressions for reproduced parser, chunk coverage, boundary and retrieval defects."""

import json
import re
from email.message import EmailMessage
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pypdf import PdfReader
from sqlalchemy import select

from backend.db.models import DocumentChunk, IngestionArtifact, KnowledgeSource
from backend.db.session import get_session_factory
from backend.processing.pipeline.chunks import ByteTokenizer, link_chunks, validate_chunks
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.runner import run_pipeline
from backend.schemas.pipeline import PipelineResult
from backend.services.ingestion import ingest_source

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
    assert all(p.source_id == "nested-html" for c in result.chunks for p in c.provenance)


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
        assert any(normalize(sentence) in normalize(c.raw_content) for c in result.chunks), sentence
    assert any("def safe_audit_example():" in c.raw_content and "return" in c.raw_content for c in result.chunks)
    assert {b.provenance.page for b in result.canonical.blocks} == set(range(1, len(pages) + 1))
    for block in result.canonical.blocks:
        assert block.provenance.bbox
        left, top, right, bottom = block.provenance.bbox
        boxes = block.structured_content["line_bboxes"]
        assert isinstance(boxes, list) and boxes
        assert all(left <= box[0] <= box[2] <= right and top <= box[1] <= box[3] <= bottom for box in boxes)
    assert all(p.source_id == "pdf-audit" for c in result.chunks for p in c.provenance)


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_utf8_boundary_preserves_complete_content(tmp_path: Path, delta: int) -> None:
    config = PipelineConfig()
    prefix = "Concept: Boundary\nSection: Boundary\n\n"
    target = config.max_chunk_tokens + delta
    content = "Boundary ASCII café 日本語 🚀 "
    while len((prefix + content + "ordinary words ").encode()) < target:
        content += "ordinary words "
    content += "x" * (target - len((prefix + content).encode()))
    path = tmp_path / "boundary.md"
    path.write_text("# Boundary\n\n" + content, encoding="utf-8")
    result = run_pipeline(path, "boundary-audit", config)
    assert "".join(chunk.raw_content for chunk in result.chunks) == content


def test_email_trailing_newline_does_not_fragment_complete_final_sentence(tmp_path: Path) -> None:
    final = "FIXFLOW_END_003 Final exception: never expose another owner\u2019s data."
    body = "Authorized audit records retain complete evidence and exception details. " * 35 + "\n\n" + final
    message = EmailMessage()
    message["Subject"] = "Email audit"
    message.set_content(body)
    path = tmp_path / "trailing-newline.eml"
    path.write_bytes(message.as_bytes())
    result = run_pipeline(path, "email-tail-audit")
    assert any(final in chunk.raw_content for chunk in result.chunks)
    assert re.sub(r"\s+", "", body) == re.sub(r"\s+", "", "".join(c.raw_content for c in result.chunks))


def test_chunk_gate_rejects_missing_tail_with_existing_block_reference(tmp_path: Path) -> None:
    path = tmp_path / "span.md"
    path.write_text(
        "# Span audit\n\n" + "Authorized evidence retains every meaningful detail. " * 70
        + "FIXFLOW_END_003 essential final exception.",
        encoding="utf-8",
    )
    result = run_pipeline(path, "span-audit")
    assert len(result.chunks) >= 2
    damaged = [chunk.model_copy(deep=True) for chunk in result.chunks[:-1]]
    parents = link_chunks(damaged)
    with pytest.raises(ValueError):
        validate_chunks(damaged, parents, result.concepts, result.canonical, ByteTokenizer(), PipelineConfig())


@pytest.mark.parametrize("extension", ["md", "json"])
def test_chunk_gate_rejects_missing_structured_part(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"structured.{extension}"
    if extension == "md":
        path.write_text("# Table\n\n| Identifier | Evidence |\n| --- | --- |\n" + "\n".join(
            f"| ROW_{i:03} | Complete authorized evidence for row {i:03}. |" for i in range(30)
        ))
    else:
        path.write_text(json.dumps({"records": [{"id": i, "evidence": f"Complete record {i:03}."} for i in range(30)]}))
    config = PipelineConfig(max_chunk_tokens=220, min_chunk_tokens=8)
    result = run_pipeline(path, "structured-span-audit", config)
    assert len(result.chunks) > 1
    damaged = [chunk.model_copy(deep=True) for chunk in result.chunks[:-1]]
    parents = link_chunks(damaged)
    with pytest.raises(ValueError, match="missing structured content"):
        validate_chunks(damaged, parents, result.concepts, result.canonical, ByteTokenizer(), config)


@pytest.mark.anyio
async def test_heading_only_unique_term_is_retrievable(client: httpx.AsyncClient) -> None:
    term = "ZXQHEADING47391"
    accepted = await client.post(
        "/api/documents",
        files={"file": ("heading.md", f"# {term}\n\nThis body retains complete authorized evidence.".encode())},
    )
    assert accepted.status_code == 202
    await ingest_source(UUID(accepted.json()["id"]))
    response = await client.post("/api/debug", json={"error": term})
    assert response.status_code == 200
    assert response.json()["sources"], "Persisted heading/context is absent from the full-text index"
    foreign = await client.post("/api/debug", json={"error": term}, headers={"X-FixFlow-User-Id": "user_other"})
    assert foreign.status_code == 200 and foreign.json()["sources"] == []
    assert (
        await client.get(f"/api/sources/{accepted.json()['id']}", headers={"X-FixFlow-User-Id": "user_other"})
    ).status_code == 404


@pytest.mark.anyio
async def test_underscore_unique_term_is_retrievable(client: httpx.AsyncClient) -> None:
    term = "ZXQ_FIXFLOW_47391"
    accepted = await client.post(
        "/api/documents",
        files={"file": ("identifier.md", f"# Identifier audit\n\n{term} retains authorized evidence.".encode())},
    )
    assert accepted.status_code == 202
    await ingest_source(UUID(accepted.json()["id"]))
    response = await client.post("/api/debug", json={"error": term})
    assert response.status_code == 200
    assert response.json()["sources"], "Query splitting must agree with PostgreSQL document lexemes"


@pytest.mark.anyio
@pytest.mark.parametrize("extension", ["html", "xml", "pdf", "md"])
async def test_repaired_formats_preserve_all_stages_three_ingestions(
    client: httpx.AsyncClient, extension: str
) -> None:
    start = "FIXFLOW_START_001 ZXQBEGIN47391 Complete authorized introduction."
    middle = "FIXFLOW_MIDDLE_002 ZXQMIDDLE47391 Unicode café 日本語 🚀."
    end = "FIXFLOW_END_003 ZXQEND47391 Final exception remains complete."
    if extension == "html":
        raw = f"<h1>Audit</h1><div>{start}</div><p>{middle}</p><footer>{end}</footer>".encode()
    elif extension == "xml":
        raw = f"<guide>{start}<section>{middle}</section><section>{end}</section></guide>".encode()
    elif extension == "pdf":
        raw = (FIXTURES / "integrity-audit.pdf").read_bytes()
    else:
        prefix = "Concept: Boundary\nSection: Boundary\n\n"
        body = start + " " + middle + " "
        while len((prefix + body + "ordinary words " + end).encode()) <= 1025:
            body += "ordinary words "
        body += "x" * (1025 - len((prefix + body + end).encode())) + end
        assert len((prefix + body).encode()) == 1025
        raw = ("# Boundary\n\n" + body).encode()
    source_id = None
    snapshots = []
    for _ in range(3):
        options: dict[str, str] = {} if source_id is None else {"update_source_id": str(source_id), "force": "true"}
        accepted = await client.post("/api/documents", files={"file": (f"integrity.{extension}", raw)}, data=options)
        assert accepted.status_code == 202, accepted.text
        identifier = UUID(accepted.json()["id"])
        assert source_id in (None, identifier)
        source_id = identifier
        await ingest_source(identifier)
        async with get_session_factory()() as db:
            source = await db.get(KnowledgeSource, identifier)
            artifact = await db.get(IngestionArtifact, identifier)
            records = list(
                await db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.source_id == identifier)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            assert source and source.owner_id == "user_test" and source.status == "ready_for_embedding"
            assert artifact
            parsed = PipelineResult.model_validate(artifact.result)
            canonical = "\n".join(b.content for b in parsed.canonical.blocks)
            concepts = "\n".join(c.markdown for c in parsed.concepts)
            chunks = "\n".join(c.content for c in records)
            for text in [canonical, concepts, chunks]:
                assert all(text.count(marker) == 1 for marker in SENTINELS)
                positions = [text.index(marker) for marker in SENTINELS]
                assert positions == sorted(positions)
                assert all(value in text for value in ["café", "日本語", "🚀"])
            assert records and len(records) == len(parsed.chunks)
            assert all(c.meta["raw_content"] == c.content for c in records)
            by_id = {c.chunk_id: c for c in parsed.chunks}
            for record in records:
                expected_provenance = [p.model_dump(mode="json") for p in by_id[record.chunk_id].provenance]
                assert record.meta["provenance"] == expected_provenance
            assert all(p.source_id == str(identifier) for c in parsed.chunks for p in c.provenance)
            snapshots.append((canonical, [(c.id, c.chunk_id, c.content, c.content_hash) for c in records]))
        for query in ["ZXQBEGIN47391", "ZXQMIDDLE47391", "ZXQEND47391"]:
            found = await client.post("/api/debug", json={"error": query})
            assert found.status_code == 200
            assert found.json()["sources"] and found.json()["sources"][0]["id"] in {c.chunk_id for c in records}
            assert query in found.json()["sources"][0]["excerpt"]
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
