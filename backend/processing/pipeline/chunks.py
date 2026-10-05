"""Concept → structure → selective sentence refinement → token limits, without overlap."""

import ast
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, cast

from defusedxml import ElementTree as XML  # type: ignore[import-untyped]

from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.parsers import render_table
from backend.schemas.pipeline import Block, CanonicalDocument, Chunk, ChunkParent, Concept, digest


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


def context(concept: Concept, unit: Unit) -> str:
    # Context is deterministic source metadata, never a model-generated factual summary.
    return f"Concept: {concept.title}\nSection: {' / '.join(unit.path)}\n\n"


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
    value: object, prefix: Callable[[str], str], tokenizer: Tokenizer, config: PipelineConfig, path: str
) -> Iterable[tuple[str, str]]:
    body = json.dumps(value, ensure_ascii=False, indent=2)
    if fits(body, prefix(path), tokenizer, config):
        yield path, body
    elif isinstance(value, dict):
        for key, child in value.items():
            # Preserve the original key and its subtree rather than splitting serialized bytes.
            wrapped = {str(key): child}
            rendered = json.dumps(wrapped, ensure_ascii=False, indent=2)
            if fits(rendered, prefix(path), tokenizer, config):
                yield path, rendered
            else:
                escaped_key = str(key).replace("~", "~0").replace("/", "~1")
                yield from tree_parts(child, prefix, tokenizer, config, f"{path}/{escaped_key}")
    elif isinstance(value, list):
        group: list[object] = []
        for child in value:
            rendered = json.dumps([*group, child], ensure_ascii=False, indent=2)
            if group and not fits(rendered, prefix(path), tokenizer, config):
                yield path, json.dumps(group, ensure_ascii=False, indent=2)
                group = []
            if not fits(json.dumps([child], ensure_ascii=False, indent=2), prefix(path), tokenizer, config):
                yield from tree_parts(child, prefix, tokenizer, config, path)
            else:
                group.append(child)
        if group:
            yield path, json.dumps(group, ensure_ascii=False, indent=2)
    else:
        raise ValueError("Structured scalar exceeds token budget; no safe structural split")


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
                return block.section_path if path in block.section_path else [*block.section_path, path]

            def tree_context(path: str) -> str:
                return context(concept, Unit("", kind, [block], section_for(path)))

            tree_units = list(
                tree_parts(value, tree_context, tokenizer, config, str(block.structured_content.get("path", "$")))
            )
            units = [Unit(content, kind, [block], section_for(path)) for path, content in tree_units]
            if any(not fits(u.content, context(concept, u), tokenizer, config) for u in units):
                raise ValueError("Structured path context exceeds the token budget")
            return units
        elif language == "xml":
            root = XML.fromstring(block.content)
            parts = [XML.tostring(item, encoding="unicode") for item in root]
            if not parts or any(not fits(part, prefix, tokenizer, config) for part in parts):
                raise ValueError("XML subtree exceeds token budget")
        elif language == "python":
            tree = ast.parse(block.content)
            lines = block.content.splitlines(keepends=True)
            parts = []
            for node in tree.body:
                start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]]) - 1
                end = node.end_lineno or node.lineno
                part = "".join(lines[start:end])
                if not fits(part, prefix, tokenizer, config):
                    raise ValueError("Python function/class exceeds token budget; atomic code cannot be split safely")
                parts.append(part)
        else:
            raise ValueError("Code exceeds token budget; configure a language AST adapter")
    else:
        if "trace_ids" in block.structured_content:
            raise ValueError("Log trace exceeds token budget; keep stack traces intact")
        parts = prose_parts(block.content, prefix, tokenizer, config)
    return [Unit(p, kind, [block], block.section_path) for p in parts if p.strip()]


def same_context(left: Unit, right: Unit) -> bool:
    return left.path == right.path and left.kind == right.kind and left.kind not in {"table", "code", "figure"}


def chunk_concept(
    document: CanonicalDocument, concept: Concept, tokenizer: Tokenizer, config: PipelineConfig
) -> list[Chunk]:
    by_id = {b.block_id: b for b in document.blocks}
    blocks = [by_id[i] for i in concept.source_block_ids]
    substantive = [b for b in blocks if b.type not in {"heading", "title", "metadata"}]

    def evidence_key(block: Block) -> tuple[str, tuple[str, ...]]:
        path = block.section_path[1:] if concept.resolved_concept_ids else block.section_path
        return block.content_hash, tuple(path)

    duplicate_evidence: dict[tuple[str, tuple[str, ...]], list[Block]] = {}
    for block in substantive:
        duplicate_evidence.setdefault(evidence_key(block), []).append(block)
    substantive = [items[0] for items in duplicate_evidence.values()]
    whole = Unit("\n\n".join(b.content for b in substantive), "mixed", substantive, concept.section_path)
    types = {b.type for b in substantive if b.type != "figure"}
    if len(types) == 1:
        whole.kind = next(iter(types))
    # Small coherent concepts remain whole, except mixed modalities that require separate chunkers.
    if (
        len(types) <= 1
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
                tokenizer.count(merged[-1].content) < config.min_chunk_tokens
                or tokenizer.count(unit.content) < config.min_chunk_tokens
            )
            and fits(merged[-1].content + "\n\n" + unit.content, context(concept, unit), tokenizer, config)
        ):
            merged[-1].content += "\n\n" + unit.content
            merged[-1].blocks = list({b.block_id: b for b in [*merged[-1].blocks, *unit.blocks]}.values())
        else:
            merged.append(unit)
    chunks: list[Chunk] = []
    for unit in merged:
        unit.blocks = [evidence for block in unit.blocks for evidence in duplicate_evidence[evidence_key(block)]]
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
            )
        )
    # Exact same concept/context/content can be collapsed while preserving every original evidence reference.
    unique: dict[str, Chunk] = {}
    for chunk in chunks:
        if chunk.chunk_id in unique:
            target = unique[chunk.chunk_id]
            target.source_block_ids = list(dict.fromkeys([*target.source_block_ids, *chunk.source_block_ids]))
            target.provenance = [by_id[i].provenance for i in target.source_block_ids]
        else:
            unique[chunk.chunk_id] = chunk
    return list(unique.values())


def link_chunks(chunks: list[Chunk]) -> list[ChunkParent]:
    parents: dict[str, ChunkParent] = {}
    for index, chunk in enumerate(chunks):
        previous = chunks[index - 1] if index else None
        following = chunks[index + 1] if index + 1 < len(chunks) else None
        chunk.previous_id = previous.chunk_id if previous and previous.concept_id == chunk.concept_id else None
        chunk.next_id = following.chunk_id if following and following.concept_id == chunk.concept_id else None
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
    if not chunks or len(ids) != len(chunks) or len(chunks) > config.max_chunks:
        raise ValueError("Invalid chunk identities or count")
    covered: set[str] = set()
    spans: dict[str, list[tuple[int, int]]] = {}
    block_chunks: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        parent = containers.get(chunk.parent_id)
        concept = concepts_by_id.get(chunk.concept_id)
        if (
            parent is None
            or concept is None
            or chunk.chunk_id not in parent.child_ids
            or parent.concept_id != chunk.concept_id
            or not chunk.source_block_ids
            or not set(chunk.source_block_ids) <= set(concept.source_block_ids)
            or chunk.child_ids
        ):
            raise ValueError("Invalid chunk hierarchy or evidence")
        evidence = [blocks[i] for i in chunk.source_block_ids]
        if chunk.provenance != [b.provenance for b in evidence]:
            raise ValueError("Lost chunk provenance")
        if (
            chunk.retrieval_content
            != context(concept, Unit(chunk.raw_content, chunk.content_type, evidence, chunk.section_path))
            + chunk.raw_content
        ):
            raise ValueError("Chunk context is not deterministic")
        if chunk.content_type not in {"code", "table"}:
            unique_evidence = list({b.content_hash: b for b in evidence}.values())
            combined = "\n\n".join(b.content for b in unique_evidence)
            if chunk.raw_content not in combined:
                raise ValueError("Chunk text is not grounded in source blocks")
            matches: list[tuple[int, int]] = []
            start = combined.find(chunk.raw_content)
            while start >= 0:
                end = start + len(chunk.raw_content)
                if matches and start <= matches[-1][1]:
                    matches[-1] = (matches[-1][0], end)
                else:
                    matches.append((start, end))
                start = combined.find(chunk.raw_content, start + 1)
            offset = 0
            for block in unique_evidence:
                intervals = [
                    (max(start - offset, 0), min(end - offset, len(block.content)))
                    for start, end in matches
                    if start < offset + len(block.content) and end > offset
                ]
                for reference in evidence:
                    if reference.content_hash == block.content_hash:
                        spans.setdefault(reference.block_id, []).extend(intervals)
                offset += len(block.content) + 2
        measured = tokenizer.count(chunk.retrieval_content)
        if (
            not chunk.raw_content.strip()
            or tokenizer.count(chunk.raw_content) < config.min_chunk_tokens
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
                if other is None or other.concept_id != chunk.concept_id or getattr(other, reverse) != chunk.chunk_id:
                    raise ValueError("Invalid chunk neighbor")
        if chunk.content_type == "table" and not (chunk.raw_content.startswith("|") and "---" in chunk.raw_content):
            raise ValueError("Table chunk lacks headers")
        if chunk.content_type == "code":
            languages = {b.structured_content.get("language") for b in evidence}
            if languages == {"python"}:
                ast.parse(chunk.raw_content)
        covered.update(chunk.source_block_ids)
        for block_id in chunk.source_block_ids:
            block_chunks.setdefault(block_id, []).append(chunk)
    expected = {b.block_id for b in document.blocks if b.type not in {"heading", "title", "metadata"}}
    if not expected <= covered:
        raise ValueError("Unexplained missing block content in chunks")
    for block in document.blocks:
        if block.block_id not in expected:
            continue
        if block.type in {"code", "table"}:
            concept = next(c for c in concepts if block.block_id in c.source_block_ids)
            for unit in split_block(block, concept, tokenizer, config):
                if not any(
                    block.block_id in c.source_block_ids
                    and (
                        c.section_path == unit.path
                        or (unit.path == block.section_path and c.section_path == concept.section_path)
                    )
                    and unit.content in c.raw_content
                    for c in block_chunks[block.block_id]
                ):
                    raise ValueError("Unexplained missing structured content in chunks")
        else:
            cursor = 0
            for start, end in sorted(spans.get(block.block_id, [])):
                if block.content[cursor:start].strip():
                    raise ValueError("Unexplained missing source span in chunks")
                cursor = max(cursor, end)
            if block.content[cursor:].strip():
                raise ValueError("Unexplained missing source span in chunks")
    for parent in parents:
        if len(parent.child_ids) != len(set(parent.child_ids)) or any(
            i not in ids or ids[i].parent_id != parent.parent_id for i in parent.child_ids
        ):
            raise ValueError("Invalid parent children")
