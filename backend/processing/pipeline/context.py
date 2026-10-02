"""Generic source provenance and access evidence; this module has no provider dependencies."""

import yaml  # type: ignore[import-untyped]

from backend.processing.okf import parse_concept
from backend.processing.pipeline.concepts import bundle
from backend.schemas.pipeline import PipelineResult


def apply_context(result: PipelineResult, context: dict[str, object]) -> None:
    if not context:
        return
    result.canonical.metadata["source_envelope"] = context
    for concept in result.concepts:
        concept.source_context = context
        parsed = parse_concept(concept.markdown, concept.concept_id + ".md")
        permissions = context.get("permissions", {})
        application_acl = (
            {
                key: permissions[key]
                for key in ("visibility", "application_owner", "provider_resource", "verified_at")
                if key in permissions
            }
            if isinstance(permissions, dict)
            else {}
        )
        metadata = {**parsed.frontmatter, "permissions": application_acl, "provenance": context.get("provenance", {})}
        concept.markdown = (
            "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n" + parsed.body
        )
    for chunk in result.chunks:
        chunk.source_context = context
    result.bundle = bundle(result.concepts)
