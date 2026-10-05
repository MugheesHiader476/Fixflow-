"""Complete canonical evidence and exact, independently verifiable fragment selectors."""

import json
from collections.abc import Iterator
from typing import Literal, cast

from backend.processing.pipeline.context import validate_source_context
from backend.processing.pipeline.parsers import render_table
from backend.schemas.pipeline import (
    AtomicUnit,
    Block,
    BlockType,
    CanonicalDocument,
    Chunk,
    Concept,
    Provenance,
    UnitSlice,
    digest,
)


def unit_id(block: Block) -> str:
    return "u-" + digest([block.provenance.source_id, block.block_id])[:40]


def character_slice(block: Block, start: int = 0, end: int | None = None) -> UnitSlice:
    end = len(block.content) if end is None else end
    line_start, line_end, line_scope = source_lines(
        block.content, start, end, block.type, block.structured_content, block.provenance
    )
    paths = symbol_paths(block.structured_content, line_start, line_end)
    return UnitSlice(
        unit_id=unit_id(block),
        character_start=start,
        character_end=end,
        line_start=line_start,
        line_end=line_end,
        line_scope=line_scope,
        symbol_paths=paths,
    )


def source_lines(
    content: str,
    start: int,
    end: int,
    kind: BlockType,
    structure: dict[str, object],
    provenance: Provenance,
) -> tuple[int | None, int | None, Literal["fragment", "unit", "unknown"]]:
    first = provenance.line_start
    if first is None:
        return None, None, "unknown"
    if kind == "table" or (kind == "code" and structure.get("language") in {"json", "yaml"} and "value" in structure):
        # Serialization adds/rearranges lines; only the original unit's source range is known.
        return first, provenance.line_end, "unit"
    last = first + content[:end].count("\n") - int(content[:end].endswith("\n"))
    return first + content[:start].count("\n"), last, "fragment"


def symbol_paths(structure: dict[str, object], line_start: int | None, line_end: int | None) -> list[list[str]]:
    symbols = structure.get("symbols")
    if not isinstance(symbols, list) or line_start is None or line_end is None:
        return []
    return [
        symbol["path"]
        for symbol in symbols
        if isinstance(symbol, dict)
        and isinstance(symbol.get("path"), list)
        and all(isinstance(p, str) for p in symbol["path"])
        and isinstance(symbol.get("line_start"), int)
        and isinstance(symbol.get("line_end"), int)
        and symbol["line_start"] <= line_end
        and symbol["line_end"] >= line_start
    ]


def atomic_units(document: CanonicalDocument, concepts: list[Concept]) -> list[AtomicUnit]:
    validate_source_context(document.source)
    owners = {i: c.concept_id for c in concepts for i in c.direct_block_ids}
    return [
        AtomicUnit(
            unit_id=unit_id(block),
            source_id=document.source.source_id,
            canonical_document_id=document.document_id,
            concept_id=owners[block.block_id],
            block_id=block.block_id,
            order_index=index,
            content_type=block.type,
            section_path=block.section_path,
            content=block.content,
            structure=block.structured_content,
            provenance=block.provenance,
            content_hash=block.content_hash,
            authorization_scope=document.source.authorization_scope,
        )
        for index, block in enumerate(document.blocks)
    ]


def validate_units(units: list[AtomicUnit], document: CanonicalDocument, concepts: list[Concept]) -> None:
    validate_source_context(document.source)
    expected = atomic_units(document, concepts)
    if units != expected:
        raise ValueError("Atomic units lost identity, order, authorization or canonical evidence")


def pointer_node(value: object, pointer: str) -> object:
    for escaped in pointer.split("/")[1:] if pointer else []:
        key = escaped.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list):
            value = value[int(key)]
        else:
            raise ValueError("Invalid structured selector")
    return value


def leaves(value: object, pointer: str = "") -> Iterator[str]:
    if isinstance(value, dict) and value:
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from leaves(child, pointer + "/" + escaped)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from leaves(child, pointer + "/" + str(index))
    else:
        yield pointer


def selected_content(unit: AtomicUnit, location: UnitSlice) -> tuple[str, list[str]]:
    if location.character_start is not None and location.character_end is not None:
        if not 0 <= location.character_start < location.character_end <= len(unit.content):
            raise ValueError("Invalid atomic character range")
        line_start, line_end, line_scope = source_lines(
            unit.content,
            location.character_start,
            location.character_end,
            unit.content_type,
            unit.structure,
            unit.provenance,
        )
        if (
            location.line_scope != line_scope
            or location.line_start != line_start
            or location.line_end != line_end
            or location.symbol_paths != symbol_paths(unit.structure, line_start, line_end)
        ):
            raise ValueError("Fragment line or symbol provenance mismatch")
        return unit.content[location.character_start : location.character_end], []
    if location.row_start is not None and location.row_end is not None:
        if unit.content_type != "table":
            raise ValueError("Table selector refers to a non-table unit")
        headers, rows = unit.structure.get("headers"), unit.structure.get("rows")
        if (
            not isinstance(headers, list)
            or not isinstance(rows, list)
            or not 1 <= location.row_start <= location.row_end <= len(rows)
        ):
            raise ValueError("Invalid table row range")
        return render_table(
            cast(list[str], headers), cast(list[list[str]], rows[location.row_start - 1 : location.row_end])
        ), [str(i) for i in range(location.row_start, location.row_end + 1)]
    if location.json_pointer is not None:
        if unit.content_type != "code" or unit.structure.get("language") not in {"json", "yaml"}:
            raise ValueError("JSON selector refers to an unstructured unit")
        value = pointer_node(json.loads(unit.content), location.json_pointer)
        if location.array_start is not None and location.array_end is not None:
            if not isinstance(value, list) or not 0 <= location.array_start < location.array_end <= len(value):
                raise ValueError("Invalid structured array range")
            covered = [
                leaf
                for index in range(location.array_start, location.array_end)
                for leaf in leaves(value[index], location.json_pointer + "/" + str(index))
            ]
            value = value[location.array_start : location.array_end]
        else:
            covered = list(leaves(value, location.json_pointer))
        if location.object_key is not None:
            key = location.json_pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
            if not location.json_pointer or location.object_key != key:
                raise ValueError("Structured fragment changed its original key")
            value = {location.object_key: value}
        return json.dumps(value, ensure_ascii=False, indent=2), covered
    raise ValueError("Atomic fragment lacks an exact selector")


def coverage_report(chunks: list[Chunk], units: list[AtomicUnit]) -> dict[str, object]:
    by_id = {u.unit_id: u for u in units}
    characters: dict[str, list[tuple[int, int]]] = {}
    structures: dict[str, set[str]] = {}
    representations: dict[str, str] = {}
    duplicate_positions = 0
    for chunk in chunks:
        fragments: dict[str, str] = {}
        for location in chunk.unit_slices:
            unit = by_id.get(location.unit_id)
            if unit is None:
                raise ValueError("Unknown atomic unit reference")
            selected, paths = selected_content(unit, location)
            if location.role == "context":
                prefix = chunk.retrieval_content[: -len(chunk.raw_content)]
                if unit.content_type not in {"heading", "title", "metadata"} or selected not in prefix:
                    raise ValueError("Ungrounded structural context")
            else:
                if unit.concept_id != chunk.concept_id or unit.block_id not in chunk.source_block_ids:
                    raise ValueError("Chunk atomic evidence belongs to another concept or block")
                # Identical evidence may have several references, but is rendered only once.
                fragments.setdefault(selected, selected)
            mode = "structured" if paths else "character"
            if unit.unit_id in representations and representations[unit.unit_id] != mode:
                raise ValueError("Mixed fragment representations cannot prove independent coverage")
            representations[unit.unit_id] = mode
            if paths:
                present = structures.setdefault(unit.unit_id, set())
                duplicate_positions += len(set(paths) & present)
                present.update(paths)
            else:
                start, end = location.character_start, location.character_end
                if start is None or end is None:
                    raise ValueError("Missing atomic coverage range")
                intervals = characters.setdefault(unit.unit_id, [])
                if location.role == "content":
                    duplicate_positions += sum(
                        meaningful_count(unit.content[max(start, left) : min(end, right)])
                        for left, right in intervals
                        if left < end and right > start
                    )
                intervals.append((start, end))
                characters[unit.unit_id] = merge_intervals(intervals)
        expected_text = "\n\n".join(fragments.values())
        if chunk.raw_content != expected_text:
            raise ValueError("Chunk content does not match its atomic selectors")
    missing: list[dict[str, object]] = []
    total = covered = 0
    for unit in units:
        if unit.unit_id in structures:
            if unit.content_type == "table":
                rows = unit.structure.get("rows")
                if not isinstance(rows, list):
                    raise ValueError("Invalid canonical table")
                expected = {str(i) for i in range(1, len(rows) + 1)}
            else:
                expected = set(leaves(json.loads(unit.content)))
            absent = expected - structures[unit.unit_id]
            total += len(expected)
            covered += len(expected - absent)
            if absent:
                missing.append({"unit_id": unit.unit_id, "block_id": unit.block_id, "paths": sorted(absent)})
        else:
            count = meaningful_count(unit.content)
            intervals = characters.get(unit.unit_id, [])
            covered_count = sum(meaningful_count(unit.content[left:right]) for left, right in intervals)
            total += count
            covered += covered_count
            if covered_count != count:
                cursor = 0
                gaps: list[tuple[int, int]] = []
                for left, right in [*intervals, (len(unit.content), len(unit.content))]:
                    if unit.content[cursor:left].strip():
                        gaps.append((cursor, left))
                    cursor = right
                missing.append(
                    {
                        "unit_id": unit.unit_id,
                        "block_id": unit.block_id,
                        "missing_character_ranges": gaps,
                        "missing_characters": count - covered_count,
                    }
                )
    return {
        "coverage_percent": covered / max(1, total) * 100,
        "canonical_elements": len(units),
        "covered_elements": len(units) - len(missing),
        "uncovered_elements": missing,
        "duplicate_content_positions": duplicate_positions,
        "method": "exact character spans, table row ranges and JSON-pointer leaves; headings verified in context",
    }


def meaningful_count(text: str) -> int:
    return sum(not character.isspace() for character in text)


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def chunk_order(chunks: list[Chunk], units: list[AtomicUnit]) -> list[tuple[int, int]]:
    by_id = {u.unit_id: u for u in units}
    leaf_positions: dict[str, dict[str, int]] = {}
    order: list[tuple[int, int]] = []
    for chunk in chunks:
        positions: list[tuple[int, int]] = []
        for location in chunk.unit_slices:
            if location.role != "content":
                continue
            unit = by_id[location.unit_id]
            offset = location.character_start or location.row_start or 0
            if location.json_pointer is not None:
                if unit.unit_id not in leaf_positions:
                    leaf_positions[unit.unit_id] = {path: i for i, path in enumerate(leaves(json.loads(unit.content)))}
                _, covered = selected_content(unit, location)
                offset = min(leaf_positions[unit.unit_id][path] for path in covered)
            positions.append((unit.order_index, offset))
        if not positions:
            raise ValueError("Chunk has no ordered content evidence")
        order.append(min(positions))
    return order
