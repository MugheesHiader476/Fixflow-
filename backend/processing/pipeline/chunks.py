"""Concept → structure → selective sentence refinement → token limits, without overlap."""

import ast
import json
import re
import textwrap
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise
from typing import Protocol, cast

from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.parsers import render_table
from backend.processing.pipeline.units import atomic_units, character_slice, chunk_order, coverage_report, unit_id
from backend.schemas.pipeline import Block, CanonicalDocument, Chunk, ChunkParent, Concept, UnitSlice, digest


class Tokenizer(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class ByteTokenizer:
    """Conservative UTF-8 byte budget; not a claim about a particular model's tokens."""

    name = "utf8_bytes"

    @staticmethod
    def count(text: str) -> int:
        if len(text) > 16000:
            return len(text.encode())
        return _byte_count(text)


@lru_cache(maxsize=4096)
def _byte_count(text: str) -> int:
    return len(text.encode())


class TiktokenTokenizer:
    def __init__(self, encoding: str) -> None:
        import tiktoken  # type: ignore[import-not-found]  # noqa: PLC0415

        self.encoding = tiktoken.get_encoding(encoding)
        self.name = f"tiktoken:{encoding}"
        self.count = lru_cache(maxsize=4096)(self._count)

    def _count(self, text: str) -> int:
        return len(self.encoding.encode(text, disallowed_special=()))


def tokenizer_for(config: PipelineConfig) -> Tokenizer:
    return TiktokenTokenizer(config.tokenizer_encoding or "") if config.tokenizer == "tiktoken" else ByteTokenizer()


@dataclass
class Unit:
    content: str
    kind: str
    blocks: list[Block]
    path: list[str]
    slices: list[UnitSlice] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.slices:
            self.slices = [character_slice(block) for block in self.blocks]


def context(concept: Concept, unit: Unit) -> str:
    # Context is deterministic source metadata, never a model-generated factual summary.
    aliases = "Aliases: " + "; ".join(concept.aliases) + "\n" if concept.resolved_concept_ids else ""
    return f"Concept: {concept.title}\nSection: {' / '.join(unit.path)}\n{aliases}\n"


def fits(content: str, prefix: str, tokenizer: Tokenizer, config: PipelineConfig) -> bool:
    return tokenizer.count(prefix + content) <= config.max_chunk_tokens


def prose_parts(text: str, prefix: str, tokenizer: Tokenizer, config: PipelineConfig) -> list[str]:
    # Only called for an oversized structural block. Preserve exact source spans, including whitespace.
    pieces = re.split(r"(?<=[.!?])(?=\s+)|(?<=\n)(?=\n)", text) if config.semantic_refinement else [text]
    result: list[str] = []
    pending = ""
    previous_words: set[str] = set()
    for piece in pieces:
        words = set(re.findall(r"\w+", piece.casefold()))
        similarity = len(words & previous_words) / max(1, len(words | previous_words))
        topic_break = config.semantic_refinement and previous_words and similarity < config.semantic_breakpoint
        if pending and (
            not fits(pending + piece, prefix, tokenizer, config)
            or (topic_break and tokenizer.count(pending) >= config.min_chunk_tokens)
        ):
            result.append(pending)
            pending = ""
        window = config.max_chunk_tokens if tokenizer.name == "utf8_bytes" else config.max_chunk_tokens * 16
        while len(piece) > window or not fits(piece, prefix, tokenizer, config):
            low, high = 1, min(len(piece), window)
            while low < high:
                middle = (low + high + 1) // 2
                if fits(piece[:middle], prefix, tokenizer, config):
                    low = middle
                else:
                    high = middle - 1
            if not fits(piece[:low], prefix, tokenizer, config):
                raise ValueError("Chunk context leaves no content budget")
            cut = piece.rfind(" ", 0, low + 1)
            cut = cut + 1 if cut > low // 2 else low
            if pending:
                result.append(pending)
                pending = ""
            result.append(piece[:cut])
            piece = piece[cut:]
        pending += piece
        previous_words = words
    if pending:
        result.append(pending)
    # A word-aligned cut can strand a tiny tail. Move only the preceding suffix;
    # merging the full chunks would violate the unchanged maximum budget.
    if len(result) > 1 and result[-1].strip() and tokenizer.count(result[-1]) < config.min_chunk_tokens:
        previous, tail = result[-2:]
        cuts = list(range(len(previous) - 1, 0, -1))
        word_cuts = [cut for cut in cuts if previous[cut - 1].isspace()]
        for cut in [*word_cuts, *cuts]:
            candidate = previous[cut:] + tail
            if tokenizer.count(candidate) >= config.min_chunk_tokens:
                if not fits(candidate, prefix, tokenizer, config):
                    continue
                if tokenizer.count(previous[:cut]) >= config.min_chunk_tokens:
                    result[-2:] = [previous[:cut], candidate]
                    break
    return result


def tree_parts(
    value: object,
    prefix: Callable[[str], str],
    tokenizer: Tokenizer,
    config: PipelineConfig,
    path: str,
    identifier: str,
) -> Iterable[tuple[str, str, UnitSlice]]:
    body = json.dumps(value, ensure_ascii=False, indent=2)
    if fits(body, prefix(path), tokenizer, config):
        yield path, body, UnitSlice(unit_id=identifier, json_pointer=path, boundary_kind="subtree")
    elif isinstance(value, dict):
        for key, child in value.items():
            # Preserve the original key and its subtree rather than splitting serialized bytes.
            wrapped = {str(key): child}
            rendered = json.dumps(wrapped, ensure_ascii=False, indent=2)
            escaped_key = str(key).replace("~", "~0").replace("/", "~1")
            child_path = f"{path}/{escaped_key}"
            if fits(rendered, prefix(child_path), tokenizer, config):
                yield (
                    child_path,
                    rendered,
                    UnitSlice(
                        unit_id=identifier, json_pointer=child_path, object_key=str(key), boundary_kind="subtree"
                    ),
                )
            else:
                yield from tree_parts(child, prefix, tokenizer, config, child_path, identifier)
    elif isinstance(value, list):
        group: list[object] = []
        start = 0
        for index, child in enumerate(value):
            rendered = json.dumps([*group, child], ensure_ascii=False, indent=2)
            if group and not fits(rendered, prefix(path), tokenizer, config):
                yield (
                    path,
                    json.dumps(group, ensure_ascii=False, indent=2),
                    UnitSlice(
                        unit_id=identifier,
                        json_pointer=path,
                        array_start=start,
                        array_end=index,
                        boundary_kind="subtree",
                    ),
                )
                group = []
                start = index
            if not fits(json.dumps([child], ensure_ascii=False, indent=2), prefix(path), tokenizer, config):
                yield from tree_parts(child, prefix, tokenizer, config, f"{path}/{index}", identifier)
                start = index + 1
            else:
                group.append(child)
        if group:
            yield (
                path,
                json.dumps(group, ensure_ascii=False, indent=2),
                UnitSlice(
                    unit_id=identifier,
                    json_pointer=path,
                    array_start=start,
                    array_end=len(value),
                    boundary_kind="subtree",
                ),
            )
    else:
        raise ValueError("Structured scalar exceeds token budget; no safe structural split")


def python_spans(text: str, prefix: str, tokenizer: Tokenizer, config: PipelineConfig) -> list[tuple[int, int]]:
    """Split definitions only between complete AST statements, preserving exact original spans."""
    tree = ast.parse(text)
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    def divide(start: int, end: int, node: ast.AST) -> list[tuple[int, int]]:
        if fits(text[start:end], prefix, tokenizer, config):
            return [(start, end)]
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or not node.body:
            raise ValueError("Atomic Python statement exceeds size budget")
        children = node.body
        ranges = [
            start,
            *[
                offsets[min([child.lineno, *[d.lineno for d in getattr(child, "decorator_list", [])]]) - 1]
                for child in children[1:]
            ],
            end,
        ]
        pieces: list[tuple[int, int]] = []
        for index, child in enumerate(children):
            left, right = ranges[index : index + 2]
            if left == start and right == end and not isinstance(node, ast.Module):
                raise ValueError("Atomic Python definition/header exceeds size budget")
            pieces.extend(divide(left, right, child))
        merged: list[tuple[int, int]] = []
        for left, right in pieces:
            if merged and fits(text[merged[-1][0] : right], prefix, tokenizer, config):
                merged[-1] = (merged[-1][0], right)
            else:
                merged.append((left, right))
        return merged

    spans = divide(0, len(text), tree)
    for start, end in spans:
        ast.parse(textwrap.dedent(text[start:end]))
    return spans


def list_parts(text: str, prefix: str, tokenizer: Tokenizer, config: PipelineConfig) -> list[str]:
    markers = list(re.finditer(r"(?m)^([ \t]*)(?:[-*+•]|\d+[.)])\s+", text))
    if not markers:
        return prose_parts(text, prefix, tokenizer, config)
    indentation = min(len(marker[1]) for marker in markers)
    starts = [m.start() for m in markers if len(m[1]) == indentation]
    starts[0] = 0
    items = [text[left:right] for left, right in zip(starts, [*starts[1:], len(text)], strict=True)]
    result: list[str] = []
    pending = ""
    for item in items:
        if pending and not fits(pending + item, prefix, tokenizer, config):
            result.append(pending)
            pending = ""
        if fits(item, prefix, tokenizer, config):
            pending += item
        else:
            result.extend(prose_parts(item, prefix, tokenizer, config))
    if pending:
        result.append(pending)
    return result


def split_block(block: Block, concept: Concept, tokenizer: Tokenizer, config: PipelineConfig) -> list[Unit]:
    kind = block.type
    unit = Unit(block.content, kind, [block], block.section_path)
    prefix = context(concept, unit)
    if fits(unit.content, prefix, tokenizer, config):
        return [unit]
    parts: list[str]
    if kind == "table":
        headers = cast(list[str], block.structured_content["headers"])
        rows = cast(list[list[str]], block.structured_content["rows"])
        table_units: list[Unit] = []
        group: list[list[str]] = []
        start = 1

        def table_unit(values: list[list[str]], first: int) -> Unit:
            return Unit(
                render_table(headers, values),
                kind,
                [block],
                [*block.section_path, f"Rows {first}-{first + len(values) - 1}"],
                [
                    UnitSlice(
                        unit_id=unit_id(block),
                        row_start=first,
                        row_end=first + len(values) - 1,
                        boundary_kind="row_group",
                    )
                ],
            )

        for index, row in enumerate(rows, 1):
            candidate = table_unit([*group, row], start)
            if group and (
                len(group) >= config.table_rows_per_chunk
                or not fits(candidate.content, context(concept, candidate), tokenizer, config)
            ):
                table_units.append(table_unit(group, start))
                group = []
                start = index
            single = table_unit([row], index)
            if not fits(single.content, context(concept, single), tokenizer, config):
                raise ValueError("Table row and headers exceed token budget")
            group.append(row)
        if group:
            table_units.append(table_unit(group, start))
        return table_units
    elif kind == "code":
        language = block.structured_content.get("language")
        if language in {"json", "yaml"} and "value" in block.structured_content:
            value = block.structured_content["value"]
            if "key" in block.structured_content:
                value = {str(block.structured_content["key"]): value}

            def section_for(path: str) -> list[str]:
                key = block.structured_content.get("key")
                if isinstance(key, str):
                    wrapped = "/" + key.replace("~", "~0").replace("/", "~1")
                    relative = path.removeprefix(wrapped)
                else:
                    relative = path
                absolute = str(block.structured_content.get("path", "$")) + relative
                return block.section_path if absolute in block.section_path else [*block.section_path, absolute]

            def tree_context(path: str) -> str:
                return context(concept, Unit("", kind, [block], section_for(path)))

            tree_units = list(tree_parts(value, tree_context, tokenizer, config, "", unit_id(block)))
            units = [
                Unit(content, kind, [block], section_for(path), [location]) for path, content, location in tree_units
            ]
            if any(not fits(u.content, context(concept, u), tokenizer, config) for u in units):
                raise ValueError("Structured path context exceeds the token budget")
            return units
        elif language == "xml":
            raise ValueError("XML subtree exceeds size budget; preserve its wrapper and mixed content")
        elif language == "python":
            code_units = []
            for start, end in python_spans(block.content, prefix, tokenizer, config):
                location = character_slice(block, start, end)
                location.boundary_kind = "statement"
                code_units.append(Unit(block.content[start:end], kind, [block], block.section_path, [location]))
            return code_units
        else:
            raise ValueError("Code exceeds token budget; configure a language AST adapter")
    else:
        if "trace_ids" in block.structured_content:
            raise ValueError("Log trace exceeds token budget; keep stack traces intact")
        parts = (list_parts if kind == "list" else prose_parts)(block.content, prefix, tokenizer, config)
    units = []
    start = 0
    for part in parts:
        start = block.content.index(part, start)
        if part.strip():
            location = character_slice(block, start, start + len(part))
            location.boundary_kind = "sentence" if part.rstrip().endswith((".", "!", "?")) else "hard_size"
            if kind == "list" and re.match(r"[ \t]*(?:[-*+•]|\d+[.)])\s+", part):
                next_part = block.content[start + len(part) :]
                if not next_part.strip() or re.match(r"[ \t]*(?:[-*+•]|\d+[.)])\s+", next_part):
                    location.boundary_kind = "list_item"
            units.append(Unit(part, kind, [block], block.section_path, [location]))
        start += len(part)
    return units


def same_context(left: Unit, right: Unit) -> bool:
    return left.path == right.path and left.kind == right.kind and left.kind not in {"table", "code", "figure"}


def chunk_concept(
    document: CanonicalDocument, concept: Concept, tokenizer: Tokenizer, config: PipelineConfig
) -> list[Chunk]:
    by_id = {b.block_id: b for b in document.blocks}
    blocks = [by_id[i] for i in concept.direct_block_ids]
    substantive = [b for b in blocks if b.type not in {"heading", "title", "metadata"}]

    def evidence_key(block: Block) -> tuple[str, tuple[str, ...]]:
        path = block.section_path[1:] if concept.resolved_concept_ids else block.section_path
        return block.content_hash, tuple(path)

    duplicate_evidence: dict[tuple[str, tuple[str, ...]], list[Block]] = {}
    for block in substantive:
        duplicate_evidence.setdefault(evidence_key(block), []).append(block)
    substantive = [items[0] for items in duplicate_evidence.values()]
    positions = {b.block_id: i for i, b in enumerate(document.blocks)}
    contiguous = all(positions[right.block_id] == positions[left.block_id] + 1 for left, right in pairwise(substantive))
    paths = {tuple(block.section_path) for block in substantive}
    whole_path = substantive[0].section_path if substantive and len(paths) == 1 else concept.section_path
    whole = Unit("\n\n".join(b.content for b in substantive), "mixed", substantive, whole_path)
    types = {b.type for b in substantive if b.type != "figure"}
    if len(types) == 1:
        whole.kind = next(iter(types))
    # Small coherent concepts remain whole, except mixed modalities that require separate chunkers.
    if (
        contiguous
        and len(types) <= 1
        and len({tuple(b.section_path) for b in substantive}) <= 1
        and whole.content
        and document.profile.modality not in {"log", "email", "transcript"}
        and fits(whole.content, context(concept, whole), tokenizer, config)
    ):
        units = [whole]
    else:
        units = [unit for block in substantive for unit in split_block(block, concept, tokenizer, config)]
    merged: list[Unit] = []
    for unit in units:
        if (
            config.merge_small_chunks
            and merged
            and same_context(merged[-1], unit)
            and (
                merged[-1].blocks[-1].block_id == unit.blocks[0].block_id
                or positions[unit.blocks[0].block_id] == positions[merged[-1].blocks[-1].block_id] + 1
            )
            and (
                tokenizer.count(merged[-1].content) < config.min_chunk_tokens
                or tokenizer.count(unit.content) < config.min_chunk_tokens
            )
            and fits(merged[-1].content + "\n\n" + unit.content, context(concept, unit), tokenizer, config)
        ):
            merged[-1].content += "\n\n" + unit.content
            merged[-1].blocks = list({b.block_id: b for b in [*merged[-1].blocks, *unit.blocks]}.values())
            merged[-1].slices.extend(unit.slices)
        else:
            merged.append(unit)
    chunks: list[Chunk] = []
    for unit in merged:
        unit.blocks = [evidence for block in unit.blocks for evidence in duplicate_evidence[evidence_key(block)]]
        for evidence in unit.blocks:
            if not any(location.unit_id == unit_id(evidence) for location in unit.slices):
                equivalent = next(b for b in substantive if evidence_key(b) == evidence_key(evidence))
                for location in list(unit.slices):
                    if location.unit_id != unit_id(equivalent):
                        continue
                    if location.character_start is not None:
                        duplicate = character_slice(evidence, location.character_start, location.character_end)
                        duplicate.boundary_kind = location.boundary_kind
                    else:
                        duplicate = location.model_copy(update={"unit_id": unit_id(evidence)})
                    unit.slices.append(duplicate)
        retrieval = context(concept, unit) + unit.content
        provenance = [b.provenance for b in unit.blocks]
        parent_path = unit.blocks[0].section_path if unit.kind in {"table", "code"} else unit.path
        parent = "p-" + digest([concept.concept_id, parent_path])[:32]
        content_hash = digest([unit.content, concept.concept_id, unit.path, unit.kind])
        identifier = "c-" + digest([document.source.source_id, content_hash])[:40]
        chunks.append(
            Chunk(
                chunk_id=identifier,
                concept_id=concept.concept_id,
                parent_id=parent,
                section_path=unit.path,
                source_block_ids=[b.block_id for b in unit.blocks],
                provenance=provenance,
                content_type=unit.kind,
                raw_content=unit.content,
                retrieval_content=retrieval,
                token_count=tokenizer.count(retrieval),
                content_hash=content_hash,
                source_id=document.source.source_id,
                canonical_document_id=document.document_id,
                authorization_scope=document.source.authorization_scope,
                unit_slices=unit.slices,
                byte_length=len(retrieval.encode()),
                character_length=len(retrieval),
            )
        )
    # Exact same concept/context/content can be collapsed while preserving every original evidence reference.
    unique: dict[str, Chunk] = {}
    for chunk in chunks:
        if chunk.chunk_id in unique:
            target = unique[chunk.chunk_id]
            target.source_block_ids = list(dict.fromkeys([*target.source_block_ids, *chunk.source_block_ids]))
            target.provenance = [by_id[i].provenance for i in target.source_block_ids]
            target.unit_slices.extend(location for location in chunk.unit_slices if location not in target.unit_slices)
        else:
            unique[chunk.chunk_id] = chunk
    return list(unique.values())


def link_chunks(chunks: list[Chunk]) -> list[ChunkParent]:
    parents: dict[str, ChunkParent] = {}
    for index, chunk in enumerate(chunks):
        previous = chunks[index - 1] if index else None
        following = chunks[index + 1] if index + 1 < len(chunks) else None
        chunk.previous_id = previous.chunk_id if previous else None
        chunk.next_id = following.chunk_id if following else None
        chunk.order_index = index
        if chunk.parent_id not in parents:
            parents[chunk.parent_id] = ChunkParent(parent_id=chunk.parent_id, concept_id=chunk.concept_id, child_ids=[])
        parents[chunk.parent_id].child_ids.append(chunk.chunk_id)
    return list(parents.values())


def validate_chunks(
    chunks: list[Chunk],
    parents: list[ChunkParent],
    concepts: list[Concept],
    document: CanonicalDocument,
    tokenizer: Tokenizer,
    config: PipelineConfig,
) -> None:
    ids = {c.chunk_id: c for c in chunks}
    containers = {p.parent_id: p for p in parents}
    concepts_by_id = {c.concept_id: c for c in concepts}
    blocks = {b.block_id: b for b in document.blocks}
    if not chunks or len(ids) != len(chunks) or len(chunks) > config.max_chunks or len(containers) != len(parents):
        raise ValueError("Invalid chunk identities or count")
    units = atomic_units(document, concepts)
    order = chunk_order(chunks, units)
    if order != sorted(order):
        raise ValueError("Chunks do not follow canonical evidence order")
    covered: set[str] = set()
    for index, chunk in enumerate(chunks):
        parent = containers.get(chunk.parent_id)
        concept = concepts_by_id.get(chunk.concept_id)
        if (
            parent is None
            or concept is None
            or chunk.chunk_id not in parent.child_ids
            or parent.concept_id != chunk.concept_id
            or chunk.source_id != document.source.source_id
            or chunk.canonical_document_id != document.document_id
            or chunk.authorization_scope != document.source.authorization_scope
            or not chunk.unit_slices
            or chunk.order_index != index
            or chunk.previous_id != (chunks[index - 1].chunk_id if index else None)
            or chunk.next_id != (chunks[index + 1].chunk_id if index + 1 < len(chunks) else None)
            or chunk.byte_length != len(chunk.retrieval_content.encode())
            or chunk.character_length != len(chunk.retrieval_content)
            or not chunk.source_block_ids
            or not set(chunk.source_block_ids) <= set(concept.direct_block_ids)
            or chunk.child_ids
        ):
            raise ValueError("Invalid chunk hierarchy or evidence")
        evidence = [blocks[i] for i in chunk.source_block_ids]
        references = {location.unit_id for location in chunk.unit_slices if location.role == "content"}
        if references != {unit_id(block) for block in evidence} or len(evidence) != len(set(chunk.source_block_ids)):
            raise ValueError("Chunk evidence does not match atomic references")
        parent_path = evidence[0].section_path if chunk.content_type in {"table", "code"} else chunk.section_path
        if chunk.parent_id != "p-" + digest([chunk.concept_id, parent_path])[:32]:
            raise ValueError("Invalid deterministic parent identity")
        if chunk.provenance != [b.provenance for b in evidence]:
            raise ValueError("Lost chunk provenance")
        if (
            chunk.retrieval_content
            != context(concept, Unit(chunk.raw_content, chunk.content_type, evidence, chunk.section_path))
            + chunk.raw_content
        ):
            raise ValueError("Chunk context is not deterministic")
        measured = tokenizer.count(chunk.retrieval_content)
        if (
            not chunk.raw_content.strip()
            or (
                tokenizer.count(chunk.raw_content) < config.min_chunk_tokens
                and not all(
                    location.json_pointer is not None
                    or location.row_start is not None
                    or any(
                        location.unit_id == unit_id(b)
                        and location.character_start == 0
                        and location.character_end == len(b.content)
                        for b in evidence
                    )
                    for location in chunk.unit_slices
                    if location.role == "content"
                )
            )
            or measured != chunk.token_count
            or measured > config.max_chunk_tokens
        ):
            raise ValueError("Empty, tiny or oversized chunk")
        if chunk.content_hash != digest([chunk.raw_content, chunk.concept_id, chunk.section_path, chunk.content_type]):
            raise ValueError("Invalid chunk hash")
        for name, reverse in (("previous_id", "next_id"), ("next_id", "previous_id")):
            linked_id = getattr(chunk, name)
            if linked_id:
                other = ids.get(linked_id)
                if other is None or getattr(other, reverse) != chunk.chunk_id:
                    raise ValueError("Invalid chunk neighbor")
        if chunk.content_type == "table" and not (chunk.raw_content.startswith("|") and "---" in chunk.raw_content):
            raise ValueError("Table chunk lacks headers")
        if chunk.content_type == "code":
            languages = {b.structured_content.get("language") for b in evidence}
            if languages == {"python"}:
                ast.parse(textwrap.dedent(chunk.raw_content))
        covered.update(chunk.source_block_ids)
    expected = {b.block_id for b in document.blocks if b.type not in {"heading", "title", "metadata"}}
    if not expected <= covered:
        raise ValueError("Unexplained missing block content in chunks")
    coverage = coverage_report(chunks, units)
    if coverage["uncovered_elements"]:
        raise ValueError("Unexplained missing structured content or source span in chunks")
    for parent in parents:
        if len(parent.child_ids) != len(set(parent.child_ids)) or any(
            i not in ids or ids[i].parent_id != parent.parent_id for i in parent.child_ids
        ):
            raise ValueError("Invalid parent children")
