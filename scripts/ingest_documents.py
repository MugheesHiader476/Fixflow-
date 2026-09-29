"""Organize and load the FixFlow documentation corpus.

Examples:
    python3 scripts/ingest_documents.py --organize
    python3 scripts/ingest_documents.py --organize --load
    python3 scripts/ingest_documents.py --load --output doc/processed/documents.jsonl

Install the optional loader dependencies first:
    python3 -m pip install -r requirements-ingestion.txt
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import IO

# Direct `python scripts/<command>.py` remains a supported entry point.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.processing.loaders import TEXT_EXTENSIONS, LoadedDocument, loader_for

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "doc"
ORGANIZED_DIR = SOURCE_DIR / "data"
DEFAULT_OUTPUT = SOURCE_DIR / "processed" / "documents.jsonl"

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".html", ".htm", ".csv", ".docx"}

IGNORED_DIRECTORY_NAMES = {
    "data",
    "processed",
    "python-3.14-docs-html",
    "python-3.14-docs-texinfo",
}


def file_hash(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_relative_to(file_path: Path, root: Path) -> Path:
    try:
        return file_path.relative_to(root)
    except ValueError:
        return Path(file_path.name)


def relative_source(file_path: Path, source_root: Path = SOURCE_DIR) -> str:
    return path_relative_to(file_path, source_root).as_posix()


def category_for(file_path: Path, source_root: Path = SOURCE_DIR) -> str:
    relative = path_relative_to(file_path, source_root)
    if source_root == SOURCE_DIR and relative.parts[0] == "python-3.14-docs-text":
        return "python"
    extension = file_path.suffix.lower()
    if extension in {".md", ".rst"}:
        return "guides"
    if extension == ".pdf":
        return "pdf"
    if extension in TEXT_EXTENSIONS:
        return "text"
    return "other"


def should_include(file_path: Path, source_root: Path = SOURCE_DIR) -> bool:
    if file_path.is_symlink() or not file_path.is_file():
        return False
    relative_parts = path_relative_to(file_path, source_root).parts
    if any(part in IGNORED_DIRECTORY_NAMES for part in relative_parts):
        return False
    return file_path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_allowed_file(
    file_path: Path,
    root: Path,
    excluded_names: set[str],
    max_size_bytes: float | None,
) -> bool:
    relative_name = path_relative_to(file_path, root).as_posix()
    if file_path.name in excluded_names or relative_name in excluded_names:
        return False
    return max_size_bytes is None or file_path.stat().st_size <= max_size_bytes


def discover_files(
    root: Path,
    excluded_names: set[str] | None = None,
    max_size_mb: float | None = None,
) -> list[Path]:
    if max_size_mb is not None and max_size_mb <= 0:
        raise ValueError("Maximum file size must be greater than zero")
    exclusions = excluded_names or set()
    max_size_bytes = max_size_mb * 1024 * 1024 if max_size_mb is not None else None
    return sorted(
        path
        for path in root.rglob("*")
        if should_include(path, root) and is_allowed_file(path, root, exclusions, max_size_bytes)
    )


def organize_files(
    files: list[Path],
    source_root: Path = SOURCE_DIR,
    replace: bool = False,
) -> list[Path]:
    ORGANIZED_DIR.mkdir(parents=True, exist_ok=True)
    organized: list[Path] = []

    for source in files:
        relative_parent = path_relative_to(source, source_root).parent
        target_dir = ORGANIZED_DIR / category_for(source, source_root) / relative_parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        if not target.exists() or replace:
            shutil.copy2(source, target)
        organized.append(target)

    return organized


def load_file(
    file_path: Path,
    source_root: Path = SOURCE_DIR,
) -> tuple[list[LoadedDocument], str]:
    loader = loader_for(file_path)
    if loader is None:
        return [], "unsupported"

    try:
        source_hash = file_hash(file_path)
        documents: list[LoadedDocument] = []
        for index, document in enumerate(loader.load()):
            if not document.page_content or not document.page_content.strip():
                continue
            document.metadata.update(
                {
                    "source": str(file_path),
                    "relative_path": relative_source(file_path, source_root),
                    "filename": file_path.name,
                    "file_extension": file_path.suffix.lower(),
                    "file_size_bytes": file_path.stat().st_size,
                    "document_index": index,
                    "file_hash": source_hash,
                }
            )
            documents.append(document)
        return documents, "loaded"
    except Exception as error:  # noqa: BLE001 - third-party loaders expose no common error base.
        print(f"  FAILED {relative_source(file_path, source_root)}: {error}")
        return [], "failed"


def json_safe(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def write_documents(documents: list[LoadedDocument], stream: IO[str]) -> None:
    for document in documents:
        record = {
            "page_content": document.page_content,
            "metadata": {key: json_safe(value) for key, value in document.metadata.items()},
        }
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_jsonl(
    documents: list[LoadedDocument],
    output: Path,
    append: bool = False,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with output.open("a", encoding="utf-8") as stream:
            write_documents(documents, stream)
        return

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
            write_documents(documents, stream.file)
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ingestion_dependencies_available() -> bool:
    return importlib.util.find_spec("langchain_community") is not None


def load_documents(files: list[Path], source_root: Path) -> tuple[list[LoadedDocument], Counter[str]]:
    documents: list[LoadedDocument] = []
    stats: Counter[str] = Counter()
    for number, file_path in enumerate(files, start=1):
        print(f"[{number}/{len(files)}] {file_path.name}")
        loaded, status = load_file(file_path, source_root)
        stats[status] += 1
        documents.extend(loaded)
        print(f"  {status}: {len(loaded)} document(s)")
    return documents, stats


def run(
    source_dir: Path,
    output: Path,
    organize: bool,
    load: bool,
    replace: bool,
    excluded_names: set[str],
    max_size_mb: float | None,
    append: bool,
) -> int:
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Documentation directory does not exist: {source_dir}")

    source_files = discover_files(source_dir, excluded_names, max_size_mb)
    files = organize_files(source_files, source_dir, replace=replace) if organize else source_files

    print(f"Source directory: {source_dir}")
    print(f"Supported files discovered: {len(source_files)}")
    if organize:
        print(f"Organized copies: {ORGANIZED_DIR}")

    if not load:
        print("Organization complete. Run with --load to create the JSONL document snapshot.")
        return 0
    if not ingestion_dependencies_available():
        print(
            "Missing Python dependency: langchain-community\n"
            "Install the ingestion dependencies with:\n"
            "  python3 -m pip install -r requirements-ingestion.txt"
        )
        return 1

    documents, stats = load_documents(files, source_dir)
    write_jsonl(documents, output, append=append)
    print("\nINGESTION SUMMARY")
    print(f"Files processed: {len(files)}")
    print(f"Files loaded: {stats['loaded']}")
    print(f"Files failed: {stats['failed']}")
    print(f"LangChain Documents: {len(documents)}")
    print(f"JSONL output: {output}")
    return 0 if stats["failed"] == 0 else 1


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organize",
        action="store_true",
        help="Copy supported files into doc/data categories",
    )
    parser.add_argument("--replace", action="store_true", help="Replace existing organized copies")
    parser.add_argument("--load", action="store_true", help="Load documents after organizing")
    parser.add_argument("--source", type=Path, default=None, help="Override the source directory")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSONL output path")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="Exclude a filename or relative path; repeat the option for multiple files",
    )
    parser.add_argument(
        "--max-size-mb",
        type=positive_float,
        default=None,
        help="Skip source files larger than this size in megabytes",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append records to an existing JSONL output",
    )
    return parser.parse_args(argv)


def default_source(arguments: argparse.Namespace) -> Path:
    configured_source = arguments.source
    if isinstance(configured_source, Path):
        return configured_source.resolve()
    if arguments.organize or not ORGANIZED_DIR.exists():
        return SOURCE_DIR.resolve()
    return ORGANIZED_DIR.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    return run(
        default_source(arguments),
        arguments.output.resolve(),
        arguments.organize,
        arguments.load,
        arguments.replace,
        set(arguments.exclude),
        arguments.max_size_mb,
        arguments.append,
    )


if __name__ == "__main__":
    raise SystemExit(main())
