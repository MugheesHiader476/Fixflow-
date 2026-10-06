"""Parser-independent, versioned evidence contracts. No provider output is trusted implicitly."""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BlockType = Literal[
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "caption",
    "equation",
    "code",
    "quote",
    "footnote",
    "citation",
    "metadata",
    "unknown",
]
Modality = Literal[
    "pdf",
    "docx",
    "pptx",
    "html",
    "markdown",
    "text",
    "csv",
    "xlsx",
    "json",
    "xml",
    "yaml",
    "code",
    "image",
    "audio",
    "video",
    "email",
    "log",
    "transcript",
]


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Contract):
    source_id: str = Field(min_length=1, max_length=200)
    filename: str
    mime_type: str
    extension: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(default=1, ge=1)
    ingested_at: datetime
    original_uri: str
    # Resolves to the authoritative source row, including its owner and access lease.
    authorization_scope: str = ""
    context: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_explicit(self) -> "Source":
        if self.ingested_at.utcoffset() is None:
            raise ValueError("Source timestamp requires UTC offset")
        return self


class ContentProfile(Contract):
    modality: Modality
    mime_type: str
    page_count: int | None = Field(default=None, ge=1)
    text_layer: bool | None = None
    scanned_pages: list[int] = Field(default_factory=list)
    has_tables: bool = False
    has_images: bool = False
    has_equations: bool = False
    complex_layout: bool = False
    language: str | None = None
    expected_characters: int | None = Field(default=None, ge=0)


class Provenance(Contract):
    source_id: str
    parser: str
    parser_version: str
    extraction_method: str
    element_path: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    sheet: str | None = None
    cell_range: str | None = None
    slide: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    timestamp_start: float | None = Field(default=None, ge=0)
    timestamp_end: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def ordered_locations(self) -> "Provenance":
        if self.line_start and self.line_end and self.line_end < self.line_start:
            raise ValueError("Invalid line range")
        if (
            self.timestamp_end is not None
            and self.timestamp_start is not None
            and self.timestamp_end < self.timestamp_start
        ):
            raise ValueError("Invalid time range")
        if self.bbox and (self.bbox[2] < self.bbox[0] or self.bbox[3] < self.bbox[1]):
            raise ValueError("Invalid bounding box")
        return self


class Block(Contract):
    block_id: str
    type: BlockType
    content: str
    structured_content: dict[str, object] = Field(default_factory=dict)
    section_path: list[str] = Field(default_factory=list)
    provenance: Provenance
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, object] = Field(default_factory=dict)
    content_hash: str


class Section(Contract):
    section_id: str
    title: str
    path: list[str]
    parent_id: str | None = None
    block_ids: list[str] = Field(default_factory=list)


class Asset(Contract):
    asset_id: str
    media_type: str
    reference: str
    provenance: Provenance


class Relationship(Contract):
    source_id: str
    target_id: str
    type: Literal["contains", "next", "caption_of", "references"]


class ParseQualityReport(Contract):
    passed: bool
    metrics: dict[str, float | None]
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CanonicalDocument(Contract):
    document_id: str
    source: Source
    profile: ContentProfile
    metadata: dict[str, object]
    sections: list[Section]
    blocks: list[Block]
    assets: list[Asset] = Field(default_factory=list)
    relationships: list[Relationship]
    provenance: list[Provenance]
    parse_quality: ParseQualityReport
    version: str
    content_hash: str

    @model_validator(mode="after")
    def evidence_integrity(self) -> "CanonicalDocument":
        blocks = {b.block_id: b for b in self.blocks}
        sections = {s.section_id: s for s in self.sections}
        assets = {a.asset_id: a for a in self.assets}
        all_ids = [self.document_id, *blocks, *sections, *assets]
        if (
            not self.parse_quality.passed
            or self.metadata.get("layout_uncertain")
            or not blocks
            or len(blocks) != len(self.blocks)
            or len(sections) != len(self.sections)
            or len(assets) != len(self.assets)
            or len(all_ids) != len(set(all_ids))
        ):
            raise ValueError("Invalid canonical identity or quality")
        memberships: list[str] = []
        for section in self.sections:
            if section.parent_id:
                parent = sections.get(section.parent_id)
                if parent is None or section.path[:-1] != parent.path:
                    raise ValueError("Invalid section hierarchy")
            memberships.extend(section.block_ids)
            if any(i not in blocks or blocks[i].section_path != section.path for i in section.block_ids):
                raise ValueError("Invalid section membership")
        if set(memberships) != set(blocks) or len(memberships) != len(blocks):
            raise ValueError("Canonical blocks must belong to exactly one section")
        for block in self.blocks:
            location = block.provenance
            if (
                not block.content.strip()
                or "\x00" in block.content
                or location.source_id != self.source.source_id
                or not location.parser
                or not location.parser_version
                or not location.extraction_method
                or (self.profile.page_count and location.page and location.page > self.profile.page_count)
            ):
                raise ValueError("Invalid block content or provenance")
            if block.content_hash != digest([block.type, block.content, block.structured_content]):
                raise ValueError("Invalid block hash")
            if block.type == "table":
                headers = block.structured_content.get("headers")
                rows = block.structured_content.get("rows")
                if (
                    not isinstance(headers, list)
                    or not headers
                    or not isinstance(rows, list)
                    or not rows
                    or any(not isinstance(r, list) or len(r) != len(headers) for r in rows)
                ):
                    raise ValueError("Invalid table structure")
            if block.type == "code" and not block.structured_content.get("language"):
                raise ValueError("Code language missing")
            if block.type == "figure" and block.structured_content.get("asset_id") not in assets:
                raise ValueError("Figure asset missing")
        for relation in self.relationships:
            if relation.source_id not in all_ids or relation.target_id not in all_ids:
                raise ValueError("Invalid canonical relationship")
        if self.provenance != [b.provenance for b in self.blocks]:
            raise ValueError("Incomplete canonical provenance")
        if self.content_hash != digest([b.model_dump(mode="json") for b in self.blocks]):
            raise ValueError("Invalid canonical hash")
        for asset in self.assets:
            if not asset.reference or not asset.media_type or asset.provenance.source_id != self.source.source_id:
                raise ValueError("Invalid asset provenance")
        return self


class Concept(Contract):
    concept_id: str
    title: str
    type: str
    section_path: list[str]
    source_block_ids: list[str]
    aliases: list[str] = Field(default_factory=list)
    resolved_concept_ids: list[str] = Field(default_factory=list)
    content_hash: str
    dependency_hash: str
    markdown: str
    source_context: dict[str, object] = Field(default_factory=dict)
    parent_concept_id: str | None = None
    child_concept_ids: list[str] = Field(default_factory=list)
    direct_block_ids: list[str] = Field(default_factory=list)


class AtomicUnit(Contract):
    """One ordered canonical element; fragments carry selectors into this complete evidence."""

    unit_id: str
    source_id: str
    canonical_document_id: str
    concept_id: str
    block_id: str
    order_index: int = Field(ge=0)
    content_type: BlockType
    section_path: list[str]
    content: str
    structure: dict[str, object]
    provenance: Provenance
    content_hash: str
    authorization_scope: str


class Continuation(Contract):
    """A fragment of one complete canonical unit, never a standalone syntax claim."""

    group_id: str
    index: int = Field(ge=0)
    count: int = Field(ge=2)
    full_unit_hash: str
    strategy: str
    structural_path: str = ""
    end_structural_path: str = ""
    table_row: int | None = Field(default=None, ge=0)
    table_column: int | None = Field(default=None, ge=0)
    table_row_end: int | None = Field(default=None, ge=0)
    table_column_end: int | None = Field(default=None, ge=0)


class UnitSlice(Contract):
    """Exact character span or structural selector into a canonical atomic unit."""

    unit_id: str
    role: Literal["content", "context"] = "content"
    character_start: int | None = Field(default=None, ge=0)
    character_end: int | None = Field(default=None, ge=0)
    json_pointer: str | None = None
    object_key: str | None = None
    array_start: int | None = Field(default=None, ge=0)
    array_end: int | None = Field(default=None, ge=0)
    row_start: int | None = Field(default=None, ge=1)
    row_end: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    line_scope: Literal["fragment", "unit", "unknown"] = "unknown"
    symbol_paths: list[list[str]] = Field(default_factory=list)
    continuation: Continuation | None = None
    boundary_kind: Literal[
        "element", "statement", "sentence", "hard_size", "list_item", "row_group", "subtree", "lexical"
    ] = "element"

    @model_validator(mode="after")
    def selector_is_unambiguous(self) -> "UnitSlice":
        character = self.character_start is not None or self.character_end is not None
        row = self.row_start is not None or self.row_end is not None
        structured = self.json_pointer is not None
        if sum((character, row, structured)) != 1:
            raise ValueError("An atomic slice requires exactly one selector")
        if character and (
            self.character_start is None or self.character_end is None or self.character_start >= self.character_end
        ):
            raise ValueError("Invalid character selector")
        if row and (self.row_start is None or self.row_end is None or self.row_start > self.row_end):
            raise ValueError("Invalid table selector")
        if (self.array_start is not None or self.array_end is not None) and (
            not structured or self.array_start is None or self.array_end is None or self.array_start >= self.array_end
        ):
            raise ValueError("Invalid array selector")
        if self.object_key is not None and not structured:
            raise ValueError("Object key requires a structured selector")
        if self.json_pointer and not self.json_pointer.startswith("/"):
            raise ValueError("JSON pointer requires an absolute path")
        if self.line_start is not None and self.line_end is not None and self.line_start > self.line_end:
            raise ValueError("Invalid fragment line range")
        if self.continuation is not None and (
            not character or self.role != "content" or self.continuation.index >= self.continuation.count
        ):
            raise ValueError("Continuation requires a content character selector and valid index")
        return self


class Chunk(Contract):
    chunk_id: str
    concept_id: str
    parent_id: str
    child_ids: list[str] = Field(default_factory=list)
    previous_id: str | None = None
    next_id: str | None = None
    section_path: list[str]
    source_block_ids: list[str]
    provenance: list[Provenance]
    content_type: str
    raw_content: str
    retrieval_content: str
    token_count: int = Field(gt=0)
    content_hash: str
    source_context: dict[str, object] = Field(default_factory=dict)
    source_id: str = ""
    canonical_document_id: str = ""
    authorization_scope: str = ""
    order_index: int = Field(default=0, ge=0)
    unit_slices: list[UnitSlice] = Field(default_factory=list)
    byte_length: int = Field(default=0, ge=0)
    character_length: int = Field(default=0, ge=0)


class ChunkParent(Contract):
    parent_id: str
    concept_id: str
    child_ids: list[str]


class PipelineResult(Contract):
    canonical: CanonicalDocument
    concepts: list[Concept]
    chunks: list[Chunk]
    parents: list[ChunkParent]
    bundle: dict[str, str]
    engine_version: str
    config_hash: str
    dependencies: dict[str, list[str]]
    stage_hashes: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    reused_concepts: int = 0
    atomic_units: list[AtomicUnit] = Field(default_factory=list)
    coverage: dict[str, object] = Field(default_factory=dict)
    statistics: dict[str, object] = Field(default_factory=dict)
    contract_version: int = 1
