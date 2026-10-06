"""Bounded content inspection. File extensions are hints checked against the payload."""

import csv
import hashlib
import io
import json
import mimetypes
import re
import zipfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from backend.processing.pipeline.config import PipelineConfig
from backend.schemas.pipeline import ContentProfile, Modality

CODE_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".sh": "bash",
    ".sql": "sql",
    ".rb": "ruby",
    ".php": "php",
}
EXTENSIONS: dict[str, Modality] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".json": "json",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".txt": "text",
    ".rst": "text",
    ".log": "log",
    ".eml": "email",
    ".vtt": "transcript",
    ".srt": "transcript",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tiff": "image",
    ".tif": "image",
    ".wav": "audio",
    ".mp3": "audio",
    ".mp4": "video",
    ".webm": "video",
    **dict.fromkeys(CODE_LANGUAGES, "code"),
}
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


@dataclass
class Inspection:
    profile: ContentProfile
    data: bytes
    sha256: str
    text: str | None = None
    pdf: PdfReader | None = None
    pdf_text: list[str] | None = None


def inspect_archive(data: bytes) -> tuple[str, set[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (
                len(entries) > 2000
                or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES
                or any(e.flag_bits & 1 for e in entries)
            ):
                raise ValueError("Unsafe or encrypted office archive")
            if "[Content_Types].xml" not in names:
                raise ValueError("Invalid office archive")
            if "word/document.xml" in names:
                return "docx", names
            if "ppt/presentation.xml" in names:
                return "pptx", names
            if "xl/workbook.xml" in names:
                return "xlsx", names
    except zipfile.BadZipFile as error:
        raise ValueError("Corrupt office archive") from error
    raise ValueError("Unsupported office archive")


def read_source(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Source is unavailable or exceeds processing limits")
    data = path.read_bytes()
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("Empty or oversized source")
    return data


def inspect(path: Path, config: PipelineConfig, data: bytes | None = None) -> Inspection:
    data = read_source(path) if data is None else data
    extension = path.suffix.lower()
    modality = EXTENSIONS.get(extension)
    if modality is None or modality not in config.supported_modalities:
        raise ValueError("Unsupported source modality")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    profile = ContentProfile(modality=modality, mime_type=mime)
    result = Inspection(profile=profile, data=data, sha256=hashlib.sha256(data).hexdigest())
    if b"%PDF-" in data[:1024]:
        if modality != "pdf":
            raise ValueError("File extension does not match PDF content")
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported")
            if not reader.pages or len(reader.pages) > config.max_pages:
                raise ValueError("PDF page count exceeds processing limits")
            pdf_text = [(p.extract_text() or "") for p in reader.pages]
            result.pdf, result.pdf_text = reader, pdf_text
            profile.page_count = len(pdf_text)
            profile.scanned_pages = [i + 1 for i, p in enumerate(reader.pages) if not pdf_text[i].strip() and p.images]
            profile.text_layer = any(t.strip() for t in pdf_text)
            profile.has_images = any(bool(p.images) for p in reader.pages)
            profile.expected_characters = sum(not c.isspace() for t in pdf_text for c in t)
            profile.has_tables = any(re.search(r"\S[ \t]{2,}\S", t) is not None for t in pdf_text)
            # Position information, not filename, decides whether the cheap PDF parser is sufficient.
            for page in reader.pages:
                xs: list[float] = []

                def position(
                    text: str, cm: list[float], tm: list[float], font: object, size: float, positions: list[float] = xs
                ) -> None:
                    if text.strip():
                        positions.append(float(tm[4]))
                        profile.complex_layout |= (
                            abs(tm[1]) > 0.01 or abs(tm[2]) > 0.01 or abs(cm[1]) > 0.01 or abs(cm[2]) > 0.01
                        )

                page.extract_text(visitor_text=position)
                positions = sorted(set(xs))
                profile.complex_layout |= any(b - a > float(page.mediabox.width) * 0.12 for a, b in pairwise(positions))
                contents = page.get_contents()
                if contents and len(re.findall(rb"\bm\s+[\d. -]+\bl\b", contents.get_data())) >= 5:
                    profile.has_tables = True
        except (PdfReadError, KeyError, TypeError, IndexError) as error:
            raise ValueError("Corrupt PDF") from error
        return result
    if data.startswith(b"PK"):
        detected, names = inspect_archive(data)
        if detected != modality:
            raise ValueError("File extension does not match office content")
        profile.has_images = any("/media/" in n for n in names)
        profile.has_tables = modality == "xlsx"
        profile.page_count = len([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]) or None
        if profile.page_count and profile.page_count > config.max_pages:
            raise ValueError("Office page count exceeds processing limits")
        return result
    signatures = {
        "image": data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")),
        "audio": data.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WAVE"),
        "video": data[4:8] == b"ftyp" or data.startswith(b"\x1aE\xdf\xa3"),
    }
    if modality in signatures:
        if not signatures[modality]:
            raise ValueError("File extension does not match media content")
        profile.has_images = modality in {"image", "video"}
        return result
    if modality in {"pdf", "docx", "pptx", "xlsx"}:
        raise ValueError("File extension does not match document content")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Text sources require UTF-8") from error
    if not text.strip() or "\x00" in text:
        raise ValueError("Empty or invalid text source")
    if len(text) > config.max_characters:
        raise ValueError("Extracted text exceeds processing limits")
    result.text = text
    stripped = text.lstrip()
    if re.match(r"(?is)\s*(?:<!doctype html|<html|<head|<body)", stripped):
        profile.modality, profile.mime_type = "html", "text/html"
    elif modality == "json" and stripped[0] not in '[{"0123456789-ntf':
        raise ValueError("Invalid JSON content")
    elif modality == "xml" and not stripped.startswith("<"):
        raise ValueError("Invalid XML content")
    elif modality == "email" and not re.search(r"(?im)^(From|Subject|Message-ID):", text):
        raise ValueError("Invalid email content")
    elif modality == "csv":
        try:
            rows = csv.reader(io.StringIO(text), strict=True)
            first = next(rows)
            if not first or len(first) < 2:
                raise ValueError("CSV requires a structured header")
        except (csv.Error, StopIteration) as error:
            raise ValueError("Invalid CSV content") from error
    if profile.modality == "text" and stripped.startswith(("{", "[")):
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            pass
        else:
            profile.modality, profile.mime_type = "json", "application/json"
    if profile.modality == "text" and re.search(r"(?m)^#{1,6} \S", text):
        profile.modality, profile.mime_type = "markdown", "text/markdown"
    profile.has_tables = profile.modality in {"csv", "json", "yaml", "xml"} or "|---" in text.replace(" ", "")
    profile.has_equations = "$$" in text or "\\[" in text
    profile.expected_characters = (
        sum(not c.isspace() for c in text) if profile.modality in {"text", "log", "code"} else None
    )
    return result
