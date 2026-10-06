"""Measured quality gates and explicit document hierarchy reconstruction."""

import unicodedata

from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.inspection import Inspection
from backend.processing.pipeline.parsers import Parsed
from backend.schemas.pipeline import CanonicalDocument, ParseQualityReport, Relationship, Section, Source, digest


def quality(parsed: Parsed, inspection: Inspection, config: PipelineConfig) -> ParseQualityReport:
    profile = inspection.profile
    content = "".join(b.content for b in parsed.blocks if b.type != "figure")
    failures: list[str] = []
    warnings = parsed.warnings.copy()
    garbled = sum(c == "\ufffd" or (unicodedata.category(c) == "Cc" and c not in "\n\r\t") for c in content)
    ratio = garbled / max(1, len(content))
    expected = profile.expected_characters
    text_coverage = min(1, sum(not c.isspace() for c in content) / expected) if expected else None
    pages = {b.provenance.page for b in parsed.blocks}
    expected_pages = set(range(1, (profile.page_count or 1) + 1)) if profile.modality == "pdf" else set()
    if inspection.pdf_text:
        expected_pages = {i + 1 for i, t in enumerate(inspection.pdf_text) if t.strip()} | set(profile.scanned_pages)
    coverage = len(pages & expected_pages) / len(expected_pages) if expected_pages else None
    confidence = [b.confidence for b in parsed.blocks if b.confidence is not None]
    ocr_confidence = min(confidence) if confidence else None
    hashes = [b.content_hash for b in parsed.blocks if b.type not in {"heading", "title", "figure"}]
    duplicate_rate = 1 - len(set(hashes)) / len(hashes) if hashes else 0
    metrics: dict[str, float | None] = {
        "page_coverage": coverage,
        "text_coverage": text_coverage,
        "garbled_ratio": ratio,
        "ocr_confidence": ocr_confidence,
        "reading_order_confidence": parsed.reading_order_confidence,
        "duplicate_header_footer_rate": duplicate_rate,
        "table_integrity": None,
        "heading_consistency": None,
        "layout_integrity": None,
        "figure_caption_coverage": None,
        "equation_preservation": None,
        "code_preservation": None,
    }
    if not content.strip():
        failures.append("no_extractable_text")
    if parsed.metadata.get("layout_uncertain"):
        failures.append("layout_uncertain")
    if profile.has_images and not parsed.assets:
        failures.append("unmapped_figures")
    if ratio > config.max_garbled_ratio:
        failures.append("garbled_text")
    if coverage is not None and coverage < config.min_page_coverage:
        failures.append("missing_pages")
    if text_coverage is not None and text_coverage < config.min_text_coverage:
        failures.append("missing_text")
    if ocr_confidence is not None and ocr_confidence < config.min_ocr_confidence:
        failures.append("low_ocr_confidence")
    if profile.modality == "pdf" and duplicate_rate > config.max_duplicate_rate:
        failures.append("excessive_repeated_content")
    if profile.complex_layout and parsed.reading_order_confidence is None:
        failures.append("unresolved_reading_order")
    if (
        parsed.reading_order_confidence is not None
        and parsed.reading_order_confidence < config.min_reading_order_confidence
    ):
        failures.append("low_reading_order_confidence")
    if profile.has_tables and profile.modality == "pdf" and not any(b.type == "table" for b in parsed.blocks):
        warnings.append("PDF table structure could not be confirmed")
        failures.append("unresolved_pdf_tables")
    tables = [b for b in parsed.blocks if b.type == "table"]
    if tables:
        valid = sum(
            bool(b.structured_content.get("headers")) and bool(b.structured_content.get("rows")) for b in tables
        )
        metrics["table_integrity"] = valid / len(tables)
    headings = [b for b in parsed.blocks if b.type == "heading"]
    if headings:
        metrics["heading_consistency"] = sum(bool(b.section_path) for b in headings) / len(headings)
    if profile.has_equations and not any(b.type == "equation" for b in parsed.blocks):
        warnings.append("Equation structure unavailable")
    return ParseQualityReport(
        passed=not failures, metrics=metrics, failures=failures, warnings=list(dict.fromkeys(warnings))
    )


def canonicalize(
    source: Source, inspection: Inspection, parsed: Parsed, report: ParseQualityReport, version: str
) -> CanonicalDocument:
    sections: dict[tuple[str, ...], Section] = {}
    root = Section(section_id="s-" + digest([source.source_id, []])[:32], title=source.filename, path=[])
    sections[()] = root
    document_id = "d-" + digest(source.source_id)[:32]
    relations: list[Relationship] = [Relationship(source_id=document_id, target_id=root.section_id, type="contains")]
    for block in parsed.blocks:
        for level in range(1, len(block.section_path) + 1):
            path = tuple(block.section_path[:level])
            if path not in sections:
                parent = sections[path[:-1]].section_id
                section = Section(
                    section_id="s-" + digest([source.source_id, path])[:32],
                    title=path[-1],
                    path=list(path),
                    parent_id=parent,
                )
                sections[path] = section
                relations.append(Relationship(source_id=parent, target_id=section.section_id, type="contains"))
        section = sections[tuple(block.section_path)]
        section.block_ids.append(block.block_id)
        relations.append(Relationship(source_id=section.section_id, target_id=block.block_id, type="contains"))
    for left, right in zip(parsed.blocks, parsed.blocks[1:], strict=False):
        relations.append(Relationship(source_id=left.block_id, target_id=right.block_id, type="next"))
        if right.type == "caption" and left.type == "figure":
            relations.append(Relationship(source_id=right.block_id, target_id=left.block_id, type="caption_of"))
    payload = {
        "document_id": document_id,
        "source": source,
        "profile": inspection.profile,
        "metadata": {"title": source.filename, **parsed.metadata},
        "sections": list(sections.values()),
        "blocks": parsed.blocks,
        "assets": parsed.assets,
        "relationships": relations,
        "provenance": [b.provenance for b in parsed.blocks],
        "parse_quality": report,
        "version": version,
    }
    content_hash = digest([b.model_dump(mode="json") for b in parsed.blocks])
    return CanonicalDocument.model_validate({**payload, "content_hash": content_hash})
