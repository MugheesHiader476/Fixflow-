"""Stage orchestration; outputs are released only after all hard validation gates pass."""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from backend.processing.okf import parse_concept
from backend.processing.pipeline.canonical import canonicalize, quality
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.context import apply_context, normalize_context
from backend.processing.pipeline.inspection import inspect, read_source
from backend.processing.pipeline.parsers import LayoutParser, NativeParser, OcrParser, Parser
from backend.processing.pipeline.validation import validate_result
from backend.schemas.pipeline import PipelineResult, Source, digest

ENGINE_VERSION = "3.0.0"
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
    ingested_at: datetime | None = None,
    source_context: dict[str, object] | None = None,
) -> PipelineResult:
    config = config or PipelineConfig()
    source_context = normalize_context(source_id, source_context)
    config_hash = digest(
        [
            ENGINE_VERSION,
            config.model_dump(mode="json"),
            strict_okf,
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
            validate_result(previous)
            event("cache_hit", source_id)
            cached = previous.model_copy(deep=True)
            apply_context(cached, source_context)
            validate_result(cached)
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
        quality_failures: set[str] = set()
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
                    quality_failures.update(report.failures)
                    event("fallback_triggered", source_id, reasons=report.failures)
                    continue
                canonical = canonicalize(source, inspection, parsed, report, ENGINE_VERSION)
                break
            except (ValueError, OSError, KeyError, IndexError, TypeError, RuntimeError) as error:
                event("fallback_triggered", source_id, parser=parser.name, error_type=type(error).__name__)
        if canonical is None:
            raise PipelineError(
                "Pipeline parsing failed quality validation; configure a suitable fallback parser or OCR"
                + (" (layout_uncertain; needs review)" if "layout_uncertain" in quality_failures else "")
            )
        canonical.metadata["source_envelope"] = source_context
        result = PipelineResult(
            canonical=canonical,
            engine_version=ENGINE_VERSION,
            config_hash=config_hash,
            stage_hashes={"source": source.sha256, "canonical": canonical.content_hash},
            warnings=canonical.parse_quality.warnings.copy(),
        )
        apply_context(result, source_context)
        validate_result(result)
        event("parsing_validated", source_id, blocks=len(canonical.blocks))
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
