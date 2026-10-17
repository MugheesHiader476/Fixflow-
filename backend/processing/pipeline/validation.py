"""Revalidate parsed evidence before caching or persistence."""

from backend.processing.pipeline.context import validate_source_context
from backend.schemas.pipeline import CanonicalDocument, PipelineResult, digest


def validate_result(result: PipelineResult) -> None:
    document = CanonicalDocument.model_validate(result.canonical.model_dump())
    validate_source_context(document.source)
    if result.contract_version != 3 or result.engine_version != document.version:
        raise ValueError("Incompatible parsing contract")
    if document.document_id != "d-" + digest(document.source.source_id)[:32]:
        raise ValueError("Canonical document identity mismatch")
    if document.metadata.get("source_envelope") != document.source.context:
        raise ValueError("Canonical authorization context mismatch")
    if result.stage_hashes != {"source": document.source.sha256, "canonical": document.content_hash}:
        raise ValueError("Invalid parsing stage hashes")
