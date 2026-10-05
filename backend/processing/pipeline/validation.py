"""Final, repeatable handoff gate; legacy artifacts remain readable but require reprocessing."""

from backend.processing.okf import parse_concept
from backend.processing.pipeline.chunks import Tokenizer, tokenizer_for, validate_chunks
from backend.processing.pipeline.concepts import bundle, validate_okf
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.context import validate_source_context
from backend.processing.pipeline.units import coverage_report, validate_units
from backend.schemas.pipeline import CanonicalDocument, PipelineResult, digest


def validate_result(result: PipelineResult, tokenizer: Tokenizer | None = None) -> None:
    if result.contract_version != 2:
        raise ValueError("Source requires reprocessing for the validated chunk contract")
    document = CanonicalDocument.model_validate(result.canonical.model_dump())
    validate_source_context(document.source)
    config = PipelineConfig.model_validate(document.metadata["chunk_policy"])
    tokenizer = tokenizer or tokenizer_for(config)
    if tokenizer.name != document.metadata["tokenizer"]:
        raise ValueError("Chunk tokenizer identity mismatch")
    validate_okf(result.concepts, document)
    validate_units(result.atomic_units, document, result.concepts)
    validate_chunks(result.chunks, result.parents, result.concepts, document, tokenizer, config)
    for concept in result.concepts:
        fields = parse_concept(concept.markdown, concept.concept_id + ".md").frontmatter
        permissions = document.source.context.get("permissions", {})
        expected_acl = (
            {
                key: permissions[key]
                for key in ("visibility", "application_owner", "provider_resource", "verified_at")
                if key in permissions
            }
            if isinstance(permissions, dict)
            else {}
        )
        if fields.get("permissions") != expected_acl or fields.get("provenance") != document.source.context.get(
            "provenance", {}
        ):
            raise ValueError("OKF authorization or provenance mismatch")
        if concept.source_context != document.source.context:
            raise ValueError("Concept authorization context mismatch")
    for chunk in result.chunks:
        if chunk.source_context != document.source.context:
            raise ValueError("Chunk authorization context mismatch")
        if chunk.chunk_id != "c-" + digest([document.source.source_id, chunk.content_hash])[:40]:
            raise ValueError("Non-deterministic chunk identity")
    measured = coverage_report(result.chunks, result.atomic_units)
    if result.coverage != measured or measured["uncovered_elements"] or measured["duplicate_content_positions"]:
        raise ValueError("Incomplete or incorrect content coverage report")
    if result.bundle != bundle(result.concepts):
        raise ValueError("OKF bundle disagrees with concepts")
    expected_hashes = {
        "source": document.source.sha256,
        "canonical": document.content_hash,
        "concepts": digest([c.content_hash for c in result.concepts]),
        "chunks": digest([c.content_hash for c in result.chunks]),
    }
    if result.stage_hashes != expected_hashes:
        raise ValueError("Invalid processing stage hashes")
