"""Create retrieval-ready chunks from the loaded document JSONL snapshot.

Run:
    python3 scripts/chunk_documents.py
    python3 scripts/chunk_documents.py --input doc/processed/documents.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import IO, TypeAlias

# Direct `python scripts/<command>.py` remains a supported entry point.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.processing.chunking import ChunkRecord, parse_source_record, records_for_source
from backend.processing.chunking import JsonObject as ProcessingJsonObject

JsonObject: TypeAlias = ProcessingJsonObject

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "doc" / "processed" / "documents.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "doc" / "processed" / "chunks.jsonl"


def make_chunk_records(
    input_path: Path,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[list[ChunkRecord], int]:
    records: list[ChunkRecord] = []
    seen_content: set[str] = set()
    source_documents = 0

    with input_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            item = parse_source_record(line, line_number)
            if not str(item.get("page_content") or "").strip():
                continue
            source_documents += 1
            records.extend(
                records_for_source(
                    item,
                    line_number,
                    chunk_size,
                    chunk_overlap,
                    seen_content,
                )
            )
    return records, source_documents


def write_records(records: list[ChunkRecord], stream: IO[str]) -> None:
    stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def write_jsonl(records: list[ChunkRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            write_records(records, stream.file)
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=3000)
    parser.add_argument("--chunk-overlap", type=int, default=400)
    args = parser.parse_args(argv)
    if args.chunk_size <= 0 or not 0 <= args.chunk_overlap < args.chunk_size:
        parser.error("chunk overlap must be non-negative and smaller than chunk size")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input JSONL does not exist: {input_path}")

    records, source_documents = make_chunk_records(
        input_path,
        args.chunk_size,
        args.chunk_overlap,
    )
    write_jsonl(records, output_path)
    print(f"Source documents: {source_documents}")
    print(f"Unique chunks: {len(records)}")
    print(f"Chunk size: {args.chunk_size} characters")
    print(f"Chunk overlap: {args.chunk_overlap} characters")
    print(f"Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
