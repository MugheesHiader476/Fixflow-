"""Generic source provenance and access evidence; this module has no provider dependencies."""

from backend.schemas.pipeline import PipelineResult, Source


def normalize_context(source_id: str, context: dict[str, object] | None) -> dict[str, object]:
    normalized = dict(context or {})
    if normalized.get("source_id", source_id) != source_id:
        raise ValueError("Source envelope identity mismatch")
    normalized["source_id"] = source_id
    normalized["authorization_scope"] = f"source:{source_id}"
    permissions = normalized.get("permissions")
    if permissions is not None and (
        not isinstance(permissions, dict)
        or permissions.get("visibility") != "private"
        or not isinstance(permissions.get("application_owner"), str)
        or not permissions["application_owner"]
    ):
        raise ValueError("Invalid private source authorization")
    return normalized


def validate_source_context(source: Source) -> None:
    if source.authorization_scope != f"source:{source.source_id}":
        raise ValueError("Missing source authorization reference")
    if normalize_context(source.source_id, source.context) != source.context:
        raise ValueError("Invalid normalized source context")


def apply_context(result: PipelineResult, context: dict[str, object]) -> None:
    context = normalize_context(result.canonical.source.source_id, context)
    result.canonical.source.context = context
    result.canonical.metadata["source_envelope"] = context
