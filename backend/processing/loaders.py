"""File loader selection shared by live ingestion and offline snapshots."""

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".h",
    ".cs", ".go", ".rs", ".php", ".rb", ".css", ".scss", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".sh", ".bash", ".dockerfile", ".sql", ".log",
}


class LoadedDocument(Protocol):
    page_content: str
    metadata: dict[str, object]


class DocumentLoader(Protocol):
    def load(self) -> Sequence[LoadedDocument]: ...

    def lazy_load(self) -> Iterator[LoadedDocument]: ...


def loader_for(file_path: Path) -> DocumentLoader | None:
    # Keep parser-heavy dependencies out of organization-only runs.
    from langchain_community.document_loaders import (  # noqa: PLC0415
        BSHTMLLoader,
        CSVLoader,
        Docx2txtLoader,
        PyPDFLoader,
        TextLoader,
    )

    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return PyPDFLoader(str(file_path))
    if suffix in {".html", ".htm"}:
        return BSHTMLLoader(str(file_path), open_encoding="utf-8")
    if suffix in TEXT_EXTENSIONS:
        return TextLoader(str(file_path), encoding="utf-8", autodetect_encoding=True)
    if suffix == ".csv":
        return CSVLoader(file_path=str(file_path), encoding="utf-8")
    if suffix == ".docx":
        return Docx2txtLoader(str(file_path))
    return None
