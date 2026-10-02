"""Validate and stream uploads into private, server-generated directories."""

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import anyio
from fastapi import HTTPException, UploadFile

from backend.config import get_settings

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".md", ".txt", ".rst", ".pdf", ".docx", ".csv", ".html", ".htm"}


def safe_filename(name: str) -> str:
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(basename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Unsupported document type")
    stem = re.sub(r"[^a-zA-Z0-9._-]", "-", basename[: -len(extension)])[: 255 - len(extension)]
    if not stem or stem in {".", ".."}:
        raise HTTPException(415, "Unsupported document type")
    return f"{stem}{extension}"


def validated_remote_url(value: str) -> str:
    candidate = value.strip()
    if len(candidate) > 2048:
        raise HTTPException(422, "Source URL is too long")
    try:
        parsed = urlsplit(candidate)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not (parsed.username or parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "Provide a valid HTTP(S) source URL without credentials")
    return candidate


def discard_upload(path: Path) -> None:
    # Only paths created by save_upload are passed here; never recurse over user paths.
    path.unlink(missing_ok=True)
    path.parent.rmdir()


async def save_upload(name: str, file: UploadFile | None, content: str | None) -> tuple[Path, str]:
    name = safe_filename(name)
    directory = get_settings().upload_dir / str(uuid4())
    directory.mkdir(parents=True, mode=0o700)
    path = directory / name
    digest = hashlib.sha256()
    size = 0
    try:
        async with await anyio.open_file(path, "xb") as output:
            path.chmod(0o600)
            while True:
                block = await file.read(1024 * 1024) if file else (content or "").encode("utf-8")
                if not block:
                    break
                size += len(block)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Document exceeds the 50 MB limit")
                digest.update(block)
                await output.write(block)
                if file is None:
                    break
        if not size:
            raise HTTPException(422, "Document cannot be empty")
        await asyncio.to_thread(validate_upload_content, path)
        return path, digest.hexdigest()
    except BaseException:
        discard_upload(path)
        raise
    finally:
        if file:
            await file.close()


def validate_upload_content(path: Path) -> None:
    """Bounded format checks before registration; extraction remains the worker's job."""
    import codecs  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with path.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                raise HTTPException(422, "File content is not a PDF")
        return
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise HTTPException(422, "File content is not a DOCX document")
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                    raise HTTPException(422, "DOCX archive exceeds processing limits")
                if any(entry.flag_bits & 1 for entry in entries):
                    raise HTTPException(422, "Encrypted DOCX files are not supported")
        except zipfile.BadZipFile as error:
            raise HTTPException(422, "File content is not a DOCX document") from error
        return
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    nonblank = False
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                text = decoder.decode(block)
                if "\x00" in text:
                    raise HTTPException(422, "Text documents cannot contain null bytes")
                nonblank = nonblank or bool(text.strip())
            decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise HTTPException(422, "Text documents must use UTF-8 encoding") from error
    if not nonblank:
        raise HTTPException(422, "Document must contain nonblank text")
