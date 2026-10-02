"""Local export of the same validated pipeline used by the persisted upload worker."""

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from backend.config import get_settings
from backend.processing.pipeline.execution import execute_pipeline as run_pipeline
from backend.processing.pipeline.runner import PipelineError
from backend.schemas.pipeline import PipelineResult


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--strict-okf", action="store_true")
    parser.add_argument("--source-id", help="Stable identity for incremental updates; defaults to input URI")
    parser.add_argument("--benchmark", type=int, default=0, help="Measure 1-20 cached runs after the initial run")
    options = parser.parse_args()
    if not 0 <= options.benchmark <= 20:
        parser.error("Benchmark repetitions must be between 0 and 20")
    output: Path = options.out.resolve()
    if output == options.source.resolve() or options.out.is_symlink():
        parser.error("Output must be a separate private directory")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = output / "pipeline.json"
    previous = None
    if cache.exists():
        if cache.is_symlink() or cache.stat().st_size > 200 * 1024 * 1024:
            parser.error("Unsafe pipeline cache")
        try:
            previous = PipelineResult.model_validate_json(cache.read_bytes())
        except ValidationError:
            if not options.force:
                parser.error("Invalid or incompatible pipeline cache; rerun with --force to rebuild")
    source_id = options.source_id or str(uuid5(NAMESPACE_URL, options.source.resolve().as_uri()))
    try:
        started = time.perf_counter()
        result = run_pipeline(
            options.source,
            source_id,
            get_settings().pipeline,
            previous=previous,
            force=options.force,
            strict_okf=options.strict_okf,
        )
        initial_seconds = time.perf_counter() - started
        samples: list[float] = []
        for _ in range(options.benchmark):
            started = time.perf_counter()
            run_pipeline(
                options.source, source_id, get_settings().pipeline, previous=result, strict_okf=options.strict_okf
            )
            samples.append(time.perf_counter() - started)
    except PipelineError as error:
        parser.exit(2, str(error) + "\n")
    # Replace validated export files atomically; publish pipeline.json last. Uploads use PostgreSQL instead.
    for relative, content in {**result.bundle, "pipeline.json": result.model_dump_json(indent=2)}.items():
        target = output / relative
        if not target.resolve().is_relative_to(output) or target.is_symlink():
            parser.error("Unsafe output path")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(content)
        temporary.replace(target)
    if previous:
        for relative in previous.bundle.keys() - result.bundle.keys():
            obsolete = output / relative
            if not obsolete.is_symlink() and obsolete.resolve().is_relative_to(output):
                obsolete.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "concepts": len(result.concepts),
                "chunks": len(result.chunks),
                "reused_concepts": result.reused_concepts,
                "warnings": result.warnings,
                "initial_seconds": round(initial_seconds, 4),
                "cached_median_seconds": round(statistics.median(samples), 4) if samples else None,
            }
        )
    )


if __name__ == "__main__":
    main()
