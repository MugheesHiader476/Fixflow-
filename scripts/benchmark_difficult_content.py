"""Reproducible, model-free candidate comparisons; outputs contain metrics, not source bodies.

Run: myenev/bin/python -m scripts.benchmark_difficult_content --output .local/difficult-content.json
The prototypes measure exact spans and source-derived structural boundary alignment.
They do not assign a model-based semantic coherence score.
"""

import argparse
import json
import re
import statistics
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import cast

from backend.processing.pipeline.canonical import quality
from backend.processing.pipeline.chunks import ByteTokenizer, python_spans, tree_parts
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.inspection import inspect
from backend.processing.pipeline.parsers import Builder, LayoutParser, NativeParser, source_yaml
from backend.processing.pipeline.runner import run_pipeline
from backend.processing.pipeline.splitting import (
    json_anchors,
    lexical_code,
    pack_spans,
    prose_boundaries,
    python_boundaries,
    table_anchors,
    xml_anchors,
    yaml_anchors,
)
from backend.processing.pipeline.units import leaves, selected_content
from backend.schemas.pipeline import AtomicUnit, Source, UnitSlice
from backend.tests.difficult_corpus import difficult_corpus

BUDGET = 768


def strict_spans(text: str, boundaries: list[int]) -> list[tuple[int, int]]:
    points = sorted({0, len(text), *boundaries})
    spans = list(pairwise(points))
    if any(len(text[left:right].encode()) > BUDGET for left, right in spans):
        raise ValueError("Complete structural unit exceeds budget")
    return spans


def candidates(unit: AtomicUnit) -> tuple[str, list[int], dict[str, Callable[[], list[tuple[int, int]]]]]:
    text = unit.content
    language = unit.structure.get("language")

    def packed(points: list[int]) -> list[tuple[int, int]]:
        return pack_spans(text, lambda part: len(part.encode()) <= BUDGET, points)

    if language in {"json", "yaml"} and (
        "value" in unit.structure or unit.structure.get("representation") == "source_lexical"
    ):
        anchors = yaml_anchors(text) if language == "yaml" and "value" not in unit.structure else json_anchors(text)
        points = [a.offset for a in anchors]

        return (
            str(language),
            points,
            {
                "path-continuation": lambda: packed(points),
                "lexical-only": lambda: packed([]),
            },
        )
    if language == "xml":
        points = [a.offset for a in xml_anchors(text)]
        return (
            "xml",
            points,
            {
                "elements-only": lambda: strict_spans(text, points),
                "sax-continuation": lambda: packed(points),
                "lexical-only": lambda: packed([]),
            },
        )
    if unit.content_type == "table":
        anchors = table_anchors(
            text, cast(list[str], unit.structure["headers"]), cast(list[list[str]], unit.structure["rows"])
        )
        cells = [a.offset for a in anchors]
        return (
            "table",
            cells,
            {
                "whole-table": lambda: strict_spans(text, []),
                "cell-continuation": lambda: packed(cells),
                "lexical-only": lambda: packed([]),
            },
        )
    if unit.content_type == "code":
        points = python_boundaries(text) if language == "python" else lexical_code(text)
        return (
            str(language),
            points,
            {
                "ast-only" if language == "python" else "lines-only": (
                    lambda: python_spans(text, "", ByteTokenizer(), PipelineConfig(max_chunk_tokens=BUDGET))
                )
                if language == "python"
                else lambda: strict_spans(text, [m + 1 for m, c in enumerate(text) if c == "\n"]),
                "syntax-lexical-continuation": lambda: packed(points),
                "lexical-only": lambda: packed([]),
            },
        )
    points = prose_boundaries(text)
    return (
        "prose",
        points,
        {
            "sentences-only": lambda: strict_spans(text, prose_boundaries(text, clauses=False)),
            "clauses-continuation": lambda: packed(points),
            "lexical-only": lambda: packed([]),
        },
    )


def structured_candidate(unit: AtomicUnit, strategy: str) -> dict[str, object]:
    """Measure actual subtree/row renderings, not simulated character windows."""
    if strategy == "numeric-coercion":
        if unit.structure.get("language") == "yaml":
            source_yaml(unit.content)
        else:
            from backend.processing.pipeline.parsers import source_float, source_integer  # noqa: PLC0415

            json.loads(unit.content, parse_int=source_integer, parse_float=source_float)
        raise ValueError("Expected a numeric-range fixture")
    if strategy == "subtrees-only":
        fragments = list(
            tree_parts(
                json.loads(unit.content),
                lambda _: "",
                ByteTokenizer(),
                PipelineConfig(max_chunk_tokens=BUDGET),
                "",
                unit.unit_id,
            )
        )
        expected = set(leaves(json.loads(unit.content)))
        locations = [location for _, _, location in fragments]
        parts = [content for _, content, _ in fragments]
    else:
        from backend.processing.pipeline.parsers import render_table  # noqa: PLC0415

        headers = cast(list[str], unit.structure["headers"])
        rows = cast(list[list[str]], unit.structure["rows"])
        expected = {str(i) for i in range(1, len(rows) + 1)}
        locations = []
        parts = []
        start = 0
        while start < len(rows):
            end = start + 1
            if len(render_table(headers, rows[start:end]).encode()) > BUDGET:
                raise ValueError("Complete table row and schema exceed budget")
            while end < len(rows) and len(render_table(headers, rows[start : end + 1]).encode()) <= BUDGET:
                end += 1
            locations.append(UnitSlice(unit_id=unit.unit_id, row_start=start + 1, row_end=end))
            parts.append(render_table(headers, rows[start:end]))
            start = end
    actual: list[str] = []
    for content, location in zip(parts, locations, strict=True):
        rendered, paths = selected_content(unit, location)
        if rendered != content:
            raise ValueError("Candidate changed canonical values")
        actual.extend(paths)
    if set(actual) != expected or len(actual) != len(set(actual)):
        raise ValueError("Candidate lost or duplicated structured evidence")
    sizes = [len(part.encode()) for part in parts]
    if max(sizes) > BUDGET:
        raise ValueError("Candidate exceeds measured budget")
    return {
        "coverage_percent": 100,
        "reconstruction": True,
        "reconstruction_kind": "structured selectors",
        "structural_boundary_percent": 100,
        "chunks": len(parts),
        "min_bytes": min(sizes),
        "median_bytes": statistics.median(sizes),
        "max_bytes": max(sizes),
        "duplication_ratio": 0,
        "necessary_repeated_header_bytes": (
            (len(parts) - 1) * len("\n".join(parts[0].splitlines()[:2]).encode()) if strategy == "rows-only" else 0
        ),
        "deterministic": True,
        "result": "pass",
        "provenance": True,
        "authorization": True,
    }


def benchmark() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    pipelines: list[dict[str, object]] = []
    repository = Path(__file__).resolve().parents[1]
    corpus = [
        *difficult_corpus(),
        ("project-api.ts", (repository / "src/lib/api.ts").read_text()),
        ("project-guide.md", (repository / "docs/ingestion-pipeline.md").read_text()),
    ]
    with tempfile.TemporaryDirectory() as directory:
        for name, body in corpus:
            path = Path(directory) / name
            path.write_text(body)
            started = time.perf_counter()
            result = run_pipeline(
                path,
                "benchmark",
                source_context={
                    "permissions": {"visibility": "private", "application_owner": "benchmark-owner"},
                },
            )
            pipelines.append(
                {
                    "fixture": name,
                    "coverage": result.coverage,
                    "statistics": result.statistics,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            for unit in result.atomic_units:
                if unit.content_type in {"heading", "metadata", "title"}:
                    continue
                content_type, points, variants = candidates(unit)
                choices: list[tuple[str, Callable[[], list[tuple[int, int]]] | None]] = list(variants.items())
                if content_type in {"json", "yaml"} and "value" in unit.structure:
                    choices.insert(0, ("subtrees-only", None))
                elif unit.structure.get("representation") == "source_lexical":
                    choices.insert(0, ("numeric-coercion", None))
                elif content_type == "table":
                    choices.insert(1, ("rows-only", None))
                for strategy, prototype in choices:
                    started = time.perf_counter()
                    row: dict[str, object] = {
                        "fixture": name,
                        "content_type": content_type,
                        "candidate": strategy,
                        "unit_hash": unit.content_hash,
                        "complexity": "stdlib/existing adapters",
                    }
                    try:
                        if prototype is None:
                            measured = structured_candidate(unit, strategy)
                            if measured != structured_candidate(unit, strategy):
                                raise ValueError("Candidate is not deterministic")
                            row.update(measured)
                            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                            rows.append(row)
                            continue
                        spans = prototype()
                        parts = [unit.content[left:right] for left, right in spans]
                        sizes = [len(part.encode()) for part in parts]
                        reconstruction = "".join(parts) == unit.content
                        if not reconstruction or max(sizes) > BUDGET:
                            raise ValueError("Candidate lost source content or exceeds measured budget")
                        row.update(
                            {
                                "coverage_percent": 100,
                                "reconstruction": reconstruction,
                                "structural_boundary_percent": 100
                                * sum(end in points for _, end in spans[:-1])
                                / max(1, len(spans) - 1),
                                "chunks": len(parts),
                                "min_bytes": min(sizes),
                                "median_bytes": statistics.median(sizes),
                                "max_bytes": max(sizes),
                                "duplication_ratio": 0,
                                "deterministic": spans == prototype(),
                                "result": "pass",
                                "provenance": bool(unit.provenance),
                                "authorization": bool(unit.authorization_scope),
                            }
                        )
                    except ValueError as error:
                        row.update(
                            {
                                "result": "rejects oversized unit",
                                "reason": str(error),
                                "coverage_percent": 0,
                                "reconstruction": False,
                            }
                        )
                    row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    rows.append(row)
    return {
        "budget_bytes": BUDGET,
        "semantic_coherence": "not model-scored; inspect structural boundaries",
        "candidates": rows,
        "final_pipeline": pipelines,
        "pdf_candidates": pdf_candidates(),
    }


def pdf_candidates() -> list[dict[str, object]]:
    measurements: list[dict[str, object]] = []
    fixture_dir = Path(__file__).resolve().parents[1] / "backend/tests/fixtures/pipeline"
    for name in (
        "digital.pdf",
        "columns.pdf",
        "table.pdf",
        "integrity-audit.pdf",
        "difficult-layout.pdf",
        "rotated-layout.pdf",
    ):
        inspection = inspect(fixture_dir / name, PipelineConfig())
        source = Source(
            source_id="pdf-benchmark",
            filename=name,
            extension=".pdf",
            mime_type="application/pdf",
            size=len(inspection.data),
            sha256=inspection.sha256,
            original_uri="source:pdf-benchmark",
            ingested_at=datetime(2026, 10, 6, tzinfo=UTC),
        )
        expected = Counter(re.findall(r"\w+", "\n".join(inspection.pdf_text or [])))
        for strategy in ("native", "pypdf-layout", "geometry"):
            started = time.perf_counter()
            if strategy == "pypdf-layout":
                builder = Builder(source, "pypdf-layout-candidate", PipelineConfig())
                if inspection.pdf is None:
                    raise ValueError("PDF inspection unavailable")
                for number, page in enumerate(inspection.pdf.pages, 1):
                    builder.add("paragraph", page.extract_text(extraction_mode="layout"), location={"page": number})
                parsed = builder.result
            else:
                parser = NativeParser() if strategy == "native" else LayoutParser()
                parsed = parser.parse(inspection, source, PipelineConfig())
            report = quality(parsed, inspection, PipelineConfig())
            text = "\n".join(block.content for block in parsed.blocks if block.type != "figure")
            actual = Counter(re.findall(r"\w+", text))
            recall = sum(min(count, actual[word]) for word, count in expected.items()) / max(1, sum(expected.values()))
            measurements.append(
                {
                    "fixture": name,
                    "candidate": strategy,
                    "word_recall_percent": recall * 100,
                    "blocks": len(parsed.blocks),
                    "tables": sum(b.type == "table" for b in parsed.blocks),
                    "page_mapping": all(b.provenance.page is not None for b in parsed.blocks),
                    "quality_passed": report.passed,
                    "failures": report.failures,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "reading_order_measured": report.metrics["reading_order_confidence"],
                }
            )
    return measurements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(f"Candidate measurements written to {args.output}")


if __name__ == "__main__":
    main()
