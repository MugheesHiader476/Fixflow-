"""Deterministic structure-aware passages with reconstructable canonical spans.

No model, embeddings, provider calls, or changes to canonical blocks. Token budgets
include the metadata prefix. Indivisible oversized units fail explicitly rather
than silently dropping evidence or producing unbounded prompts.
"""

import ast
import json
import re
import time
from bisect import bisect_left
from collections.abc import Iterable
from functools import lru_cache
from typing import Literal

import tiktoken

from backend.processing.pipeline.parsers import render_table
from backend.processing.pipeline.structure import lexical_code
from backend.schemas.evidence import IndexRequest, Passage, Span
from backend.schemas.pipeline import Block, CanonicalDocument, digest

TARGET_TOKENS = 450
SOFT_MAX_TOKENS = 650
DEFAULT_OVERLAP = 0
LONG_PROSE_OVERLAP = 40
SLACK_TARGET_TOKENS = 600
PASSAGE_VERSION = "1-cl100k_base"
MAX_PASSAGES = 20000
MAX_PASSAGE_BYTES = 64 * 1024 * 1024


@lru_cache(maxsize=1)
def encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def tokens(text: str) -> int:
    return len(encoding().encode(text, disallowed_special=()))


def brief(value: object, limit: int = 100) -> str:
    # Metadata is data, never instructions; prevent multiline prefix injection.
    text = re.sub(r"\s+", " ", str(value or "Unknown")[:4096]).strip()
    encoded = encoding().encode(text, disallowed_special=())
    return (
        text
        if len(encoded) <= limit
        else encoding().decode_bytes(encoded[:limit]).decode("utf-8", errors="ignore") + "…"
    )


def source_metadata(document: CanonicalDocument, labels: IndexRequest) -> dict[str, object]:
    context = document.source.context
    native = context.get("metadata")
    native = native if isinstance(native, dict) else {}
    headers = document.metadata.get("email_headers", [])
    email: dict[str, str] = {}
    if isinstance(headers, list):
        for header in headers:
            if isinstance(header, dict):
                email.setdefault(str(header.get("name", "")).lower(), str(header.get("value", "")))
    metadata: dict[str, object] = {
        "project": labels.project,
        "source": labels.title or context.get("title") or context.get("filename") or document.source.filename,
        "date": labels.document_date.isoformat()
        if labels.document_date
        else (native.get("date") or email.get("date") or native.get("created_at") or "Unknown"),
        "date_basis": "owner_supplied" if labels.document_date else "source_metadata",
        "source_version": document.source.version,
    }
    # Deliberately avoid copying arbitrary envelopes/permission/credential fields.
    if document.profile.modality == "email":
        metadata.update(
            sender=native.get("from") or email.get("from", ""),
            subject=native.get("subject") or email.get("subject", ""),
            thread_id=native.get("thread_id") or document.metadata.get("thread_id", ""),
            message_id=native.get("message_id") or document.metadata.get("message_id", ""),
        )
    if context.get("provider") == "slack" and native.get("message_ts"):
        metadata.update(
            channel_id=native.get("channel_id"),
            thread_id=native.get("thread_ts"),
            message_id=native.get("message_ts"),
            sender=native.get("author_name") or native.get("author_id"),
            connector_account_id=context.get("connector_account_id"),
        )
    return {key: value[:4096] if isinstance(value, str) else value for key, value in metadata.items()}


def prefix(metadata: dict[str, object]) -> str:
    fields = [("Project", "project"), ("Source", "source"), ("Section", "section"), ("Date", "date")]
    fields += [("Sender", "sender"), ("Subject", "subject"), ("File", "file_path"), ("Function", "function")]
    return "\n".join(f"{label}: {brief(metadata[key], 24)}" for label, key in fields if key in metadata)


def span(block: Block, start: int, end: int, *, rows: bool = False) -> Span:
    location = block.provenance.model_copy(deep=True)
    if not rows and location.line_start is not None:
        location.line_start += block.content[:start].count("\n")
        location.line_end = location.line_start + block.content[start:end].rstrip("\r\n").count("\n")
    return Span(
        block_id=block.block_id, start=start, end=end, unit="rows" if rows else "characters", provenance=location
    )


def reconstruct(blocks: dict[str, Block], location: Span) -> str:
    block = blocks[location.block_id]
    if location.unit == "rows":
        rows, headers = block.structured_content["rows"], block.structured_content["headers"]
        if (
            not isinstance(rows, list)
            or not isinstance(headers, list)
            or not 0 <= location.start < location.end <= len(rows)
        ):
            raise ValueError("Invalid table passage span")
        return render_table(
            [str(h) for h in headers], [[str(c) for c in row] for row in rows[location.start : location.end]]
        )
    if not 0 <= location.start < location.end <= len(block.content):
        raise ValueError("Invalid passage character span")
    return block.content[location.start : location.end]


def passage_id(passage: Passage) -> str:
    return (
        "p-"
        + digest(
            [
                PASSAGE_VERSION,
                passage.source_id,
                passage.source_hash,
                passage.canonical_hash,
                passage.ordinal,
                passage.group,
                passage.kind,
                passage.prefix,
                passage.content,
                [s.model_dump(mode="json") for s in passage.spans],
                passage.metadata,
                passage.warnings,
            ]
        )[:40]
    )


def validate_passage(document: CanonicalDocument, passage: Passage) -> None:
    blocks = {b.block_id: b for b in document.blocks}
    if (
        passage.source_id != document.source.source_id
        or passage.source_hash != document.source.sha256
        or passage.canonical_hash != document.content_hash
        or passage.prefix != prefix(passage.metadata)
        or passage.tokens != tokens(passage.text)
        or passage.id != passage_id(passage)
        or passage.content != "\n\n".join(reconstruct(blocks, s) for s in passage.spans)
    ):
        raise ValueError("Passage evidence integrity failed")
    for location in passage.spans:
        expected = span(blocks[location.block_id], location.start, location.end, rows=location.unit == "rows")
        if location != expected:
            raise ValueError("Passage location mismatch")


def fitting_end(text: str, start: int, end: int, budget: int) -> int:
    # Character slicing keeps Unicode intact, including tokens containing partial UTF-8 bytes.
    low, high = start + 1, end
    while low <= high:
        middle = (low + high) // 2
        if tokens(text[start:middle]) <= budget:
            low = middle + 1
        else:
            high = middle - 1
    if high <= start:
        raise ValueError("Passage metadata leaves no content budget")
    return high


def split_ranges(
    text: str, start: int, end: int, budget: int, points: list[int], overlap: int = 0
) -> Iterable[tuple[int, int]]:
    boundaries = sorted({p for p in points if start < p <= end})
    position = 0
    while start < end:
        window_end = min(end, start + max(4096, budget * 16))
        if window_end == end and tokens(text[start:end]) <= budget:
            yield start, end
            return
        maximum = fitting_end(text, start, window_end, budget)
        while position < len(boundaries) and boundaries[position] <= start:
            position += 1
        choices: list[int] = []
        while position < len(boundaries) and boundaries[position] <= maximum:
            choices.append(boundaries[position])
            position += 1
        stop = choices[-1] if choices else maximum
        yield start, stop
        previous = start
        start = stop
        if overlap:
            # Only long prose gets overlap; never cross the paragraph's boundary.
            low = max(previous + 1, stop - overlap * 12)
            recent = boundaries[bisect_left(boundaries, low) : bisect_left(boundaries, stop)]
            for boundary in recent:
                if tokens(text[boundary:stop]) <= overlap:
                    start = boundary
                    break


def code_ranges(block: Block) -> list[tuple[int, int, str]]:
    lines = block.content.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    origin = block.provenance.line_start or 1
    symbols = block.structured_content.get("symbols", [])
    candidates: list[tuple[int, int, str]] = []
    declared_classes: set[tuple[int, int, str]] = set()
    if isinstance(symbols, list):
        for symbol in symbols:
            if not isinstance(symbol, dict):
                continue
            first, last, path = symbol.get("line_start"), symbol.get("line_end"), symbol.get("path")
            if (
                isinstance(first, int)
                and isinstance(last, int)
                and isinstance(path, list)
                and 0 <= first - origin < last - origin + 1 <= len(lines)
            ):
                unit = (offsets[first - origin], offsets[last - origin + 1], ".".join(map(str, path)))
                candidates.append(unit)
                if symbol.get("kind") == "ClassDef":
                    declared_classes.add(unit)
    # Class containers give way to their methods; a function containing a nested
    # function stays whole when it fits. Lexical languages remain conservative.
    classes = declared_classes | {
        unit
        for unit in candidates
        if re.match(r"\s*(?:export\s+)?(?:class|struct|namespace)\b", block.content[unit[0] : unit[1]])
    }
    functions = [unit for unit in candidates if unit not in classes]
    leaves = []
    outer_end = -1
    for unit in sorted(functions, key=lambda item: (item[0], -item[1])):
        if unit[0] >= outer_end:
            leaves.append(unit)
            outer_end = unit[1]
    if not leaves:
        leaves = candidates
    result: list[tuple[int, int, str]] = []
    start = 0
    for first, last, name in sorted(leaves):
        if first < start:
            continue
        if first > start:
            result.append((start, first, str(block.structured_content.get("symbol") or "module")))
        result.append((first, last, name))
        start = last
    if start < len(block.content):
        result.append((start, len(block.content), str(block.structured_content.get("symbol") or "module")))
    return result


def logical_points(block: Block) -> list[int]:
    if block.structured_content.get("language") == "python":
        tree = ast.parse(block.content)
        ends = [m.end() for m in re.finditer(r"\n", block.content)] + [len(block.content)]
        return sorted({ends[n.end_lineno - 1] for n in ast.walk(tree) if isinstance(n, ast.stmt) and n.end_lineno})
    return lexical_code(block.content)


def build_passages(document: CanonicalDocument, labels: IndexRequest) -> list[Passage]:
    document = CanonicalDocument.model_validate(document.model_dump())
    base = source_metadata(document, labels)
    passages: list[Passage] = []
    blocks = {b.block_id: b for b in document.blocks}
    started = time.monotonic()
    output_bytes = 0
    document_warnings = document.parse_quality.warnings.copy()
    if document.profile.modality == "pdf" and not any(b.type == "heading" for b in document.blocks):
        document_warnings.append("Reliable PDF headings are unavailable; passages follow extracted paragraph order")
    pending: list[Span] = []
    pending_warnings: list[str] = []
    pending_metadata: dict[str, object] = {}
    pending_kind: Literal["prose", "email", "slack", "code", "table"] = "prose"
    group = "root"
    email = document.profile.modality == "email"
    slack = "channel_id" in base
    native = document.source.context.get("metadata")
    serialized_metadata = (
        "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in native.items())
        if slack and isinstance(native, dict)
        else ""
    )

    def emit(
        locations: list[Span],
        metadata: dict[str, object],
        kind: Literal["prose", "email", "slack", "code", "table"],
        warnings: list[str] | None = None,
    ) -> None:
        nonlocal output_bytes
        if len(passages) >= MAX_PASSAGES or time.monotonic() - started > 30:
            raise ValueError("Passage count or processing deadline exceeded")
        content = "\n\n".join(reconstruct(blocks, item) for item in locations)
        if not content.strip():
            return
        if len(content) > 20000:
            raise ValueError("Indivisible structured unit exceeds passage budget; needs review")
        metadata = metadata.copy()
        if kind == "table":
            location = locations[0]
            original = blocks[location.block_id]
            row_numbers = original.metadata.get("row_numbers")
            metadata["row_numbers"] = (
                row_numbers[location.start : location.end] if isinstance(row_numbers, list) else None
            )
            metadata["row_indices"] = [location.start, location.end]
            if original.provenance.cell_range and isinstance(row_numbers, list):
                match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", original.provenance.cell_range)
                if match:
                    metadata["header_cell_range"] = f"{match[1]}{match[2]}:{match[3]}{match[2]}"
                    metadata["cell_range"] = (
                        f"{match[1]}{row_numbers[location.start]}:{match[3]}{row_numbers[location.end - 1]}"
                    )
        heading = prefix(metadata)
        count = tokens(heading + "\n\n" + content)
        if count > SOFT_MAX_TOKENS:
            raise ValueError("Indivisible structured unit exceeds passage budget; needs review")
        value = Passage(
            id="pending",
            source_id=document.source.source_id,
            source_hash=document.source.sha256,
            canonical_hash=document.content_hash,
            ordinal=len(passages),
            kind=kind,
            group=group,
            prefix=heading,
            content=content,
            tokens=count,
            spans=locations,
            metadata=metadata,
            warnings=[*document_warnings, *(warnings or [])],
        )
        value.id = passage_id(value)
        output_bytes += len(value.model_dump_json().encode())
        if output_bytes > MAX_PASSAGE_BYTES:
            raise ValueError("Passage output exceeds bounded index size")
        passages.append(value)

    def flush() -> None:
        if pending:
            emit(pending.copy(), pending_metadata.copy(), pending_kind, pending_warnings.copy())
            pending.clear()
            pending_warnings.clear()

    for block in document.blocks:
        if serialized_metadata and block.content == serialized_metadata:
            continue
        section = " / ".join(block.section_path) or "Document"
        if email:
            section = str(base.get("subject") or "Email body")
        metadata = {**base, "section": section}
        if block.type in {"heading", "title"}:
            if not email and not slack:
                flush()
                group = block.block_id  # Repeated heading titles still delimit different runs.
            continue
        if block.type == "table":
            flush()
            headers, rows = block.structured_content["headers"], block.structured_content["rows"]
            if not isinstance(headers, list) or not isinstance(rows, list):
                raise ValueError("Invalid canonical table")
            metadata.update(
                table_block=block.block_id,
                sheet=block.provenance.sheet,
                cell_range=block.provenance.cell_range,
                row_numbers=block.metadata.get("row_numbers"),
                merged_ranges=block.metadata.get("merged_ranges", []),
            )
            first = 0
            for end in range(1, len(rows) + 1):
                rendered = render_table([str(h) for h in headers], [[str(c) for c in row] for row in rows[first:end]])
                over_budget = len(rendered) > 20000 or tokens(prefix(metadata) + "\n\n" + rendered) > TARGET_TOKENS
                if over_budget and end - 1 > first:
                    emit([span(block, first, end - 1, rows=True)], metadata.copy(), "table")
                    first = end - 1
                elif len(rendered) > 20000:
                    raise ValueError("Indivisible table unit exceeds passage budget; needs review")
            emit([span(block, first, len(rows), rows=True)], metadata, "table")
            continue
        if block.type == "code":
            flush()
            native = document.source.context.get("metadata")
            metadata.update(
                file_path=((native.get("file_path") or native.get("path")) if isinstance(native, dict) else None)
                or document.source.context.get("filename")
                or document.source.filename
            )
            for first, last, name in code_ranges(block):
                details = {**metadata, "function": name, "language": block.structured_content["language"]}
                budget = TARGET_TOKENS - tokens(prefix(details)) - 4
                if (
                    last - first <= 10000
                    and tokens(block.content[first:last]) + tokens(prefix(details)) + 4 <= SOFT_MAX_TOKENS
                ):
                    emit([span(block, first, last)], details, "code")
                else:
                    for left, right in split_ranges(block.content, first, last, budget, logical_points(block)):
                        emit(
                            [span(block, left, right)],
                            details.copy(),
                            "code",
                            ["Large code unit split; passage may require surrounding code"],
                        )
            continue
        kind: Literal["prose", "email", "slack", "code", "table"] = "email" if email else "slack" if slack else "prose"
        if pending and (metadata != pending_metadata or kind != pending_kind):
            flush()
        target = SLACK_TARGET_TOKENS if slack else TARGET_TOKENS
        budget = target - tokens(prefix(metadata)) - 4
        points = [m.end() for m in re.finditer(r"[.!?][\"')\]]*(?:\s+|$)|\n\s*\n", block.content)]
        point_set = set(points)
        long_prose = len(block.content) > 10000 or tokens(block.content) > SOFT_MAX_TOKENS
        overlap = LONG_PROSE_OVERLAP if kind == "prose" and long_prose else 0
        for first, last in split_ranges(block.content, 0, len(block.content), budget, points, overlap):
            location = span(block, first, last)
            if pending:
                combined = "\n\n".join(reconstruct(blocks, item) for item in [*pending, location])
                if tokens(prefix(metadata) + "\n\n" + combined) > target:
                    flush()
            pending.append(location)
            if kind != "code" and (
                (first > 0 and first not in point_set) or (last < len(block.content) and last not in point_set)
            ):
                warning = "Oversized sentence split at a character boundary; refer to its canonical span"
                if warning not in pending_warnings:
                    pending_warnings.append(warning)
            pending_metadata, pending_kind = metadata, kind
    flush()
    if not passages:
        if slack:
            raise ValueError("No message text available for indexing")
        # A heading-only document still has citable evidence.
        for block in document.blocks:
            emit([span(block, 0, len(block.content))], {**base, "section": " / ".join(block.section_path)}, "prose")
    return passages
