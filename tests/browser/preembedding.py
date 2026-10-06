"""Disposable-browser fixtures and independent SQL/prepared handoff verification.

This is test tooling, never an application endpoint. DATABASE_URL must end in _test.
"""

import argparse
import asyncio
import hashlib
import io
import json
import re
import shutil
from email import policy
from email.parser import BytesParser
from pathlib import Path
from uuid import UUID

from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import func, select, text

from backend.config import get_settings
from backend.db.models import Document, DocumentChunk, KnowledgeSource
from backend.db.session import close_database, get_session_factory
from backend.processing.pipeline.inspection import CODE_LANGUAGES, EXTENSIONS
from backend.processing.pipeline.inspection import inspect as inspect_file
from backend.processing.pipeline.units import selected_content
from backend.repositories.prepared import prepared_source
from backend.tests.difficult_corpus import difficult_corpus
from backend.tests.test_prepared_pipeline import corpus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend/tests/fixtures/pipeline"


def make_fixtures(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []

    def add(name: str, data: bytes, expected: str = "ready_for_embedding", acceptance: int = 202) -> None:
        target = directory / name
        target.write_bytes(data)
        suffix = target.suffix.lower()
        words: list[str] = []
        if suffix not in {".pdf", ".docx", ".pptx", ".xlsx", ".png"}:
            meaningful = data.decode("utf-8", errors="replace")
            if suffix in {".html", ".htm"}:
                soup = BeautifulSoup(meaningful, "html.parser")
                for hidden in soup.select("script, style"):
                    hidden.decompose()
                meaningful = soup.get_text(" ")
            elif suffix == ".eml":
                message = BytesParser(policy=policy.default).parsebytes(data)
                body = message.get_body(preferencelist=("plain", "html"))
                meaningful = str(message.get("Subject", "")) + " " + (str(body.get_content()) if body else "")
            elif suffix == ".vtt":
                meaningful = re.sub(r"^WEBVTT[^\n]*", "", meaningful)
            words = re.findall(r"[A-Za-z][A-Za-z0-9_]{4,}", meaningful)
        sentinels = list(dict.fromkeys([words[0], words[len(words) // 2], words[-1]])) if words else []
        manifest.append(
            {
                "name": name,
                "path": str(target),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "modality": EXTENSIONS.get(suffix, "unsupported"),
                "language": CODE_LANGUAGES.get(suffix),
                "expected": expected,
                "acceptance": acceptance,
                "sanity_terms": sentinels,
            }
        )

    for name, body in corpus():
        add("normal-" + name, body.encode())
    for name, body in difficult_corpus():
        add("hard-" + name, body.encode())
    # All accepted code extensions, including languages outside the AST/brace subset.
    extra = {
        "component.jsx": "export function Component() { return <p>JSX_BEGIN alpha JSX_MIDDLE omega JSX_END</p>; }",
        "component.tsx": "export function Component(): JSX.Element { "
        "return <p>TSX_BEGIN alpha TSX_MIDDLE omega TSX_END</p>; }",
        "header.h": "int HEADER_BEGIN(void);\nint HEADER_MIDDLE(void);\nint HEADER_END(void);\n",
        "class.cs": 'class Evidence { string Text = "CSHARP_BEGIN alpha CSHARP_MIDDLE omega CSHARP_END"; }',
        "script.sh": "#!/bin/sh\nprintf '%s' 'BASH_BEGIN alpha BASH_MIDDLE omega BASH_END'\n",
        "script.rb": 'def evidence\n  "RUBY_BEGIN alpha RUBY_MIDDLE omega RUBY_END"\nend\n',
        "script.php": '<?php function evidence() { return "PHP_BEGIN alpha PHP_MIDDLE omega PHP_END"; }',
        "reference.rst": "RST_BEGIN\n=========\n\nRST_MIDDLE coherent evidence.\n\nRST_END exception.\n",
        "page.htm": "<html><body><h1>HTM_BEGIN</h1><p>HTM_MIDDLE evidence.</p><footer>HTM_END</footer></body></html>",
        "mapping.yml": "start: YML_BEGIN\nvalues:\n  - YML_MIDDLE\nend: YML_END\n",
        "speech.srt": "1\n00:00:00,000 --> 00:00:02,000\nSRT_BEGIN\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nSRT_MIDDLE\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\nSRT_END\n",
        "sentinels.txt": "E2ESTART78413 "
        + "authorized evidence café 日本語 🚀 " * 100
        + " E2EMIDDLE78413 "
        + "preserved details " * 100
        + " E2EEND78413.",
        "stress.txt": "STRESS_BEGIN " + "Unicode evidence café 日本語 🚀 " * 14000 + " STRESS_END.",
        "stress.md": "# Stress\n\n"
        + "\n\n".join(f"## Section {i}\n\n" + "Evidence remains complete. " * 50 for i in range(120)),
        "stress.js": "function evidence() {\n"
        + "\n".join(f"let record{i} = 'evidence{i}';" for i in range(6000))
        + "\nreturn record5999;\n}",
        "stress.json": json.dumps({"records": [{"id": i, "value": f"evidence{i}"} for i in range(1800)]}),
        "stress.xml": "<records>"
        + "".join(f"<record id='{i}'>evidence{i}</record>" for i in range(2000))
        + "</records>",
        "stress.csv": "ID,Notes\n" + "\n".join(f"row{i},complete evidence{i}" for i in range(3000)),
    }
    for name, body in extra.items():
        add(name, body.encode())
    for name in [
        "guide.docx",
        "slides.pptx",
        "workbook.xlsx",
        "page.html",
        "message.eml",
        "events.log",
        "speech.vtt",
        "digital.pdf",
        "table.pdf",
        "columns.pdf",
        "integrity-audit.pdf",
    ]:
        add("native-" + name, (FIXTURES / name).read_bytes())
    for name in ["scanned.pdf", "scan.png", "difficult-layout.pdf", "rotated-layout.pdf"]:
        add("rejected-" + name, (FIXTURES / name).read_bytes(), "failed")
    for extension in ("jpg", "jpeg", "tif", "tiff"):
        buffer = io.BytesIO()
        with Image.open(FIXTURES / "scan.png") as image:
            image.convert("RGB").save(buffer, format="JPEG" if extension in {"jpg", "jpeg"} else "TIFF")
        add("unconfigured." + extension, buffer.getvalue(), "failed")
    for name, payload in [
        ("invalid.json", b'{"missing":'),
        ("invalid.yaml", b"key: [unclosed"),
        ("invalid.xml", b"<root>unclosed"),
        ("invalid.pdf", b"%PDF-1.7\ncorrupt"),
    ]:
        add(name, payload, "failed")
    add("zero.txt", b"", "rejected", 422)
    add("binary.exe", b"unsupported binary", "rejected", 415)
    add("invalid-utf8.txt", b"\xff\x80", "rejected", 422)
    # Valid media signatures, deliberately no transcription adapter. OCR engine unconfigured.
    for extension, signature in {
        "wav": b"RIFF0000WAVE",
        "mp3": b"ID3",
        "mp4": b"0000ftyp",
        "webm": b"\x1aE\xdf\xa3",
    }.items():
        add("unconfigured." + extension, signature + b"test boundary", "failed")
    shutil.copyfile(FIXTURES / "scan.png", directory / "retry-image.png")
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))


async def inspect_source(source_id: UUID, owner: str, fixture: Path | None) -> dict[str, object]:
    if not (get_settings().sqlalchemy_url().database or "").endswith("_test"):
        raise ValueError("Browser verification requires a disposable database ending _test")
    try:
        async with get_session_factory()() as db:
            source = await db.get(KnowledgeSource, source_id)
            if source is None or source.owner_id != owner:
                raise ValueError("Unexpected source ownership")
            raw = Path(source.path or "")
            if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest() != source.file_hash:
                raise ValueError("Original source was lost or changed")
            prepared = await prepared_source(db, source_id, owner)
            other = "user_preembedding_b" if owner == "user_preembedding_a" else "user_preembedding_a"
            if await prepared_source(db, source_id, other) is not None:
                raise ValueError("Another owner received private prepared content")
            records = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id)))
            documents = list(await db.scalars(select(Document).where(Document.source_id == source_id)))
            if source.status != "ready_for_embedding":
                if prepared is not None or records or documents:
                    raise ValueError("Failed source exposed prepared content or partial SQL projections")
                details: dict[str, object] = {}
                try:
                    profile = inspect_file(raw, get_settings().pipeline).profile
                    details = {"input_modality": profile.modality, "scanned_pages": profile.scanned_pages}
                except ValueError:
                    details = {"corrupt_or_invalid_input": True}
                return {
                    "source_id": str(source_id),
                    "status": source.status,
                    "raw_retained": True,
                    "source_hash": source.file_hash,
                    "prepared": False,
                    "error_message": source.error_message,
                    **details,
                }
            if (
                prepared is None
                or prepared.coverage["coverage_percent"] != 100
                or prepared.coverage["uncovered_elements"]
            ):
                raise ValueError("Ready source did not satisfy prepared handoff")
            if len(records) != len(prepared.chunks) or len(documents) != len(prepared.concepts):
                raise ValueError("SQL projection count mismatch")
            if any(r.embedding is not None for r in records):
                raise ValueError("Pre-embedding validation unexpectedly generated vectors")
            for unit in prepared.atomic_units:
                slices = [
                    s for c in prepared.chunks for s in c.unit_slices if s.unit_id == unit.unit_id and s.continuation
                ]
                if slices and "".join(selected_content(unit, s)[0] for s in slices) != unit.content:
                    raise ValueError("Continuation reconstruction failed")
            canonical = "\n".join(b.content for b in prepared.canonical.blocks)
            original_sanity: dict[str, object] = {"method": "source-derived first/middle/last lexical markers"}
            if fixture is not None and EXTENSIONS.get(fixture.suffix) in {"text", "code"}:
                text_content = "\n".join(b.content for b in prepared.canonical.blocks if b.type != "heading")
                if re.sub(r"\s+", "", fixture.read_text()) != re.sub(r"\s+", "", text_content):
                    raise ValueError("Source-to-canonical text/code normalization lost content")
                original_sanity = {"method": "non-whitespace character equality", "character_recall": 100}
            if fixture is not None and fixture.suffix == ".eml":
                message = BytesParser(policy=policy.default).parsebytes(fixture.read_bytes())
                if prepared.canonical.metadata.get("email_headers") != [
                    {"name": name, "value": str(value)} for name, value in message.items()
                ]:
                    raise ValueError("Email provenance headers were not retained")
            if fixture is not None and fixture.suffix == ".pdf":
                words = re.findall(r"\w+", "\n".join(p.extract_text() or "" for p in PdfReader(fixture).pages))
                actual = set(re.findall(r"\w+", canonical))
                original_sanity = {
                    "method": "native extracted-word recall; ordering separately gated",
                    "word_recall": sum(w in actual for w in words) / max(1, len(words)) * 100,
                }
                if original_sanity["word_recall"] != 100:
                    raise ValueError("Verified PDF parser lost extracted words")
            return {
                "source_id": str(source_id),
                "status": source.status,
                "parser": prepared.statistics["parser"],
                "coverage": prepared.coverage,
                "statistics": prepared.statistics,
                "prepared": True,
                "source_hash": source.file_hash,
                "source_version": prepared.canonical.source.version,
                "canonical_document_id": prepared.canonical.document_id,
                "raw_retained": True,
                "chunk_ids": [c.chunk_id for c in prepared.chunks],
                "authorization": True,
                "provenance": True,
                "original_sanity": original_sanity,
                "canonical_text": canonical if len(canonical) < 50000 else None,
                "strategies": sorted(
                    {
                        s.continuation.strategy if s.continuation else s.boundary_kind
                        for c in prepared.chunks
                        for s in c.unit_slices
                    }
                ),
                "continuation_groups": prepared.coverage.get("continuation_groups", 0),
            }
    finally:
        await close_database()


async def reset_database() -> None:
    if not (get_settings().sqlalchemy_url().database or "").endswith("_test"):
        raise ValueError("Refusing to reset a non-test database")
    try:
        async with get_session_factory().begin() as db:
            await db.execute(text("TRUNCATE knowledge_sources, debug_sessions, saved_solutions CASCADE"))
    finally:
        await close_database()


async def integrity() -> dict[str, object]:
    if not (get_settings().sqlalchemy_url().database or "").endswith("_test"):
        raise ValueError("Browser verification requires a disposable database ending _test")
    try:
        async with get_session_factory()() as db:
            sources = list(await db.scalars(select(KnowledgeSource)))
            ids = {s.id for s in sources}
            documents = list(await db.scalars(select(Document)))
            chunks = list(await db.scalars(select(DocumentChunk)))
            by_document = {d.id: d for d in documents}
            if any(d.source_id not in ids for d in documents) or any(
                c.source_id not in ids
                or c.document_id not in by_document
                or by_document[c.document_id].source_id != c.source_id
                for c in chunks
            ):
                raise ValueError("Orphan/cross-source SQL projections")
            if any(c.embedding is not None for c in chunks):
                raise ValueError("Embeddings unexpectedly exist")
            if any(s.status not in {"ready_for_embedding", "failed"} for s in sources):
                raise ValueError("Stuck source jobs")
            return {
                "sources": len(sources),
                "documents": len(documents),
                "chunks": len(chunks),
                "source_ids": sorted(str(s.id) for s in sources),
                "orphans": 0,
                "embeddings": 0,
                "statuses": {state: sum(s.status == state for s in sources) for state in {s.status for s in sources}},
                "source_chunk_counts": {
                    str(s.id): await db.scalar(
                        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.source_id == s.id)
                    )
                    for s in sources
                },
            }
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["fixtures", "inspect", "reset", "integrity"])
    parser.add_argument("target")
    parser.add_argument("--owner", default="user_preembedding_a")
    parser.add_argument("--fixture", type=Path)
    arguments = parser.parse_args()
    if arguments.operation == "fixtures":
        make_fixtures(Path(arguments.target))
    elif arguments.operation == "reset":
        asyncio.run(reset_database())
    elif arguments.operation == "integrity":
        print(json.dumps(asyncio.run(integrity())))
    else:
        print(json.dumps(asyncio.run(inspect_source(UUID(arguments.target), arguments.owner, arguments.fixture))))


if __name__ == "__main__":
    main()
