"""Stage orchestration; outputs are released only after all hard validation gates pass."""

import hashlib
import json
import logging
import statistics
import time
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from backend.processing.okf import parse_concept
from backend.processing.pipeline.canonical import canonicalize, quality
from backend.processing.pipeline.chunks import Tokenizer, chunk_concept, link_chunks, tokenizer_for, validate_chunks
from backend.processing.pipeline.concepts import (
    bundle,
    dependency_hash,
    extract_concepts,
    resolve_concepts,
    validate_okf,
)
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.context import apply_context, normalize_context
from backend.processing.pipeline.inspection import inspect, read_source
from backend.processing.pipeline.parsers import LayoutParser, NativeParser, OcrParser, Parser
from backend.processing.pipeline.units import (
    atomic_units,
    character_slice,
    chunk_order,
    coverage_report,
    validate_units,
)
from backend.processing.pipeline.validation import validate_result
from backend.schemas.pipeline import Chunk, PipelineResult, Source, digest

ENGINE_VERSION = "2.0"
logger = logging.getLogger(__name__)


class PipelineError(ValueError):
    """Only a safe, stage-level message escapes the processing boundary."""


def event(name: str, source_id: str, **fields: object) -> None:
    payload = {"event": name, "source_id": source_id, **fields}
    logger.info(json.dumps(payload), extra={"pipeline": payload})


def configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def run_pipeline(
    path: Path,
    source_id: str,
    config: PipelineConfig | None = None,
    *,
    previous: PipelineResult | None = None,
    force: bool = False,
    strict_okf: bool = False,
    parsers: list[Parser] | None = None,
    tokenizer: Tokenizer | None = None,
    ingested_at: datetime | None = None,
    source_context: dict[str, object] | None = None,
) -> PipelineResult:
    config = config or PipelineConfig()
    source_context = normalize_context(source_id, source_context)
    tokenizer_identity = tokenizer.name if tokenizer else f"{config.tokenizer}:{config.tokenizer_encoding}"
    config_hash = digest(
        [
            ENGINE_VERSION,
            config.model_dump(mode="json"),
            strict_okf,
            tokenizer_identity,
            {
                k: v
                for k, v in (source_context or {}).items()
                if k
                not in {"fetched_at", "permissions", "content_hash", "source_version", "modified_at", "ingested_at"}
            },
        ]
    )
    try:
        data = read_source(path)
        source_hash = hashlib.sha256(data).hexdigest()
        event("source_received", source_id, size=len(data), hash=source_hash)
        if (
            previous
            and previous.canonical.source.source_id == source_id
            and config.cache_enabled
            and not force
            and previous.canonical.source.sha256 == source_hash
            and previous.config_hash == config_hash
        ):
            # Revalidate persisted output rather than trusting a stale or corrupt cache envelope.
            tokenizer = tokenizer or tokenizer_for(config)
            validate_units(previous.atomic_units, previous.canonical, previous.concepts)
            validate_okf(previous.concepts, previous.canonical)
            validate_chunks(previous.chunks, previous.parents, previous.concepts, previous.canonical, tokenizer, config)
            validate_result(previous, tokenizer)
            event("cache_hit", source_id)
            cached = previous.model_copy(deep=True)
            apply_context(cached, source_context)
            validate_result(cached, tokenizer)
            return cached
        inspection = inspect(path, config, data)
        if previous and previous.canonical.source.source_id != source_id:
            raise PipelineError("Pipeline previous output belongs to another source")
        source = Source(
            source_id=source_id,
            filename=path.name,
            extension=path.suffix.lower(),
            mime_type=inspection.profile.mime_type,
            size=len(inspection.data),
            sha256=inspection.sha256,
            original_uri=f"source:{source_id}",
            authorization_scope=f"source:{source_id}",
            context=source_context,
            ingested_at=ingested_at or datetime.now(UTC),
            version=(
                previous.canonical.source.version + int(previous.canonical.source.sha256 != inspection.sha256)
                if previous
                else 1
            ),
        )
        if strict_okf:
            try:
                parse_concept(inspection.text or "", path.name)
            except ValueError as error:
                raise PipelineError(
                    "Invalid OKF concept: check Markdown frontmatter, type and metadata limits."
                ) from error
        event("route_selected", source_id, modality=inspection.profile.modality)
        available: list[Parser] = parsers if parsers is not None else [NativeParser(), LayoutParser(), OcrParser()]
        if parsers is None:
            registered = {p.name: p for p in available}
            for reference in config.adapter_factories:
                module, factory = reference.split(":", 1)
                adapter = getattr(import_module(module), factory)()
                if not isinstance(adapter, Parser) or adapter.tier not in {1, 2, 3}:
                    raise PipelineError("Configured parser adapter does not implement the parser contract")
                registered[adapter.name] = adapter
            available = list(registered.values())
        order = {name: i for i, name in enumerate(config.parser_priority)}
        adapters = sorted(
            (p for p in available if p.name in order and p.supports(inspection)), key=lambda p: (p.tier, order[p.name])
        )
        if not adapters and inspection.profile.modality in {"audio", "video"}:
            raise PipelineError("Audio/video ingestion requires a configured transcription adapter.")
        canonical = None
        for parser in adapters:
            if parser.name == "ocr" and not config.ocr_enabled:
                continue
            event("parser_selected", source_id, parser=parser.name, tier=parser.tier)
            started = time.monotonic()
            try:
                parsed = parser.parse(inspection, source, config)
                if time.monotonic() - started > config.parser_timeout_seconds:
                    raise PipelineError("Parser exceeded configured time budget")
                report = quality(parsed, inspection, config)
                event(
                    "quality",
                    source_id,
                    passed=report.passed,
                    metrics=report.metrics,
                    duration_seconds=round(time.monotonic() - started, 4),
                )
                if not report.passed:
                    event("fallback_triggered", source_id, reasons=report.failures)
                    continue
                canonical = canonicalize(source, inspection, parsed, report, ENGINE_VERSION)
                break
            except (ValueError, OSError, KeyError, IndexError, TypeError, RuntimeError) as error:
                event("fallback_triggered", source_id, parser=parser.name, error_type=type(error).__name__)
        if canonical is None:
            raise PipelineError(
                "Pipeline parsing failed quality validation; configure a suitable fallback parser or OCR"
            )
        tokenizer = tokenizer or tokenizer_for(config)
        canonical.metadata["tokenizer"] = tokenizer.name
        canonical.metadata["source_envelope"] = source_context
        canonical.metadata["chunk_policy"] = config.model_dump(mode="json")
        reusable = previous if previous and previous.config_hash == config_hash and not force else None
        concepts = extract_concepts(canonical, ENGINE_VERSION, reusable)
        concepts, merged_count = resolve_concepts(concepts, canonical)
        event("concepts_resolved", source_id, concepts=len(concepts), merged=merged_count)
        warnings = validate_okf(concepts, canonical)
        event("okf_validated", source_id, concepts=len(concepts), warnings=len(warnings))
        atomic = atomic_units(canonical, concepts)
        old_concepts = {c.concept_id: c for c in reusable.concepts} if reusable else {}
        chunks: list[Chunk] = []
        reused = 0
        by_id = {b.block_id: b for b in canonical.blocks}
        for concept in concepts:
            current_dependency = dependency_hash([by_id[i] for i in concept.source_block_ids])
            old = old_concepts.get(concept.concept_id)
            if old and old.dependency_hash == current_dependency and reusable:
                chunks.extend(c.model_copy(deep=True) for c in reusable.chunks if c.concept_id == concept.concept_id)
                reused += 1
                event("concept_cache_hit", source_id, concept_hash=concept.content_hash)
            else:
                chunks.extend(chunk_concept(canonical, concept, tokenizer, config))
        # Restore global canonical order, including direct parent evidence after a child section.
        ordered = chunk_order(chunks, atomic)
        chunks = [chunk for _, chunk in sorted(zip(ordered, chunks, strict=True), key=lambda pair: pair[0])]
        for chunk in chunks:
            chunk.canonical_document_id = canonical.document_id
            chunk.unit_slices = [r for r in chunk.unit_slices if r.role == "content"]
            prefix = chunk.retrieval_content[: -len(chunk.raw_content)]
            for block in canonical.blocks:
                if block.type in {"heading", "title", "metadata"} and block.content in prefix:
                    location = character_slice(block)
                    location.role = "context"
                    chunk.unit_slices.append(location)
        parents = link_chunks(chunks)
        validate_chunks(chunks, parents, concepts, canonical, tokenizer, config)
        event("chunks_validated", source_id, count=len(chunks), reused_concepts=reused)
        dependencies: dict[str, list[str]] = {b.block_id: [] for b in canonical.blocks}
        dependencies.update({c.concept_id: [] for c in concepts})
        dependencies.update({u.unit_id: [] for u in atomic})
        for concept in concepts:
            for identifier in concept.source_block_ids:
                dependencies[identifier].append(concept.concept_id)
        for chunk in chunks:
            dependencies[chunk.concept_id].append(chunk.chunk_id)
            for identifier in dict.fromkeys(r.unit_id for r in chunk.unit_slices):
                dependencies[identifier].append(chunk.chunk_id)
        stage_hashes = {
            "source": source.sha256,
            "canonical": canonical.content_hash,
            "concepts": digest([c.content_hash for c in concepts]),
            "chunks": digest([c.content_hash for c in chunks]),
        }
        result = PipelineResult(
            canonical=canonical,
            concepts=concepts,
            chunks=chunks,
            parents=parents,
            bundle=bundle(concepts),
            engine_version=ENGINE_VERSION,
            config_hash=config_hash,
            dependencies=dependencies,
            stage_hashes=stage_hashes,
            warnings=[*canonical.parse_quality.warnings, *warnings],
            reused_concepts=reused,
            atomic_units=atomic,
            coverage=coverage_report(chunks, atomic),
            statistics={
                "canonical_elements": len(canonical.blocks),
                "concepts": len(concepts),
                "atomic_units": len(atomic),
                "chunks": len(chunks),
                "min_bytes": min(c.byte_length for c in chunks),
                "max_bytes": max(c.byte_length for c in chunks),
                "mean_bytes": statistics.mean(c.byte_length for c in chunks),
                "median_bytes": statistics.median(c.byte_length for c in chunks),
                "parser": canonical.blocks[0].provenance.parser,
                "duplicate_canonical_elements": len(atomic) - len({u.content_hash for u in atomic}),
                "hard_size_fragments": sum(
                    r.boundary_kind == "hard_size" for c in chunks for r in c.unit_slices if r.role == "content"
                ),
                "validation_failures": 0,
                "uncovered_elements": 0,
            },
            contract_version=2,
        )
        apply_context(result, source_context or {})
        validate_okf(result.concepts, result.canonical)
        validate_units(result.atomic_units, result.canonical, result.concepts)
        validate_result(result, tokenizer)
        event("coverage_validated", source_id, coverage=result.coverage, statistics=result.statistics)
        return result
    except PipelineError:
        event("validation_failed", source_id)
        raise
    except Exception as error:
        # Intentional application boundary: parser exceptions never leak source text or local paths.
        event("validation_failed", source_id, error_type=type(error).__name__)
        raise PipelineError(
            "Pipeline validation failed; check source structure and processing configuration"
        ) from error
