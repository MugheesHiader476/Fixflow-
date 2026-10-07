"""Fixed gold labels and selection/metric correctness; never invokes a model in CI."""

import json
from typing import cast

import pytest

from backend.services.embedding_profiles import PROFILES
from scripts.benchmark_embeddings import DATASET, BenchmarkResult, Fixture, Query, choose, metrics


def test_fixed_benchmark_has_nonempty_gold_and_diverse_sources() -> None:
    fixture = cast(Fixture, json.loads(DATASET.read_text()))
    assert 50 <= len(fixture["queries"]) <= 100
    assert all(q["gold_chunk_ids"] for q in fixture["queries"])
    assert {"code", "configuration", "table", "multilingual", "continuation", "project-code", "identifier"} <= {
        q["type"] for q in fixture["queries"]
    }
    assert len([d for d in fixture["documents"] if d["type"] == "distractor"]) >= 20


def test_metrics_measure_all_gold_and_first_relevant_rank() -> None:
    query: Query = {"id": "q", "type": "code", "text": "example", "gold_document": "d", "gold_chunk_ids": ["a", "b"]}
    result = metrics([query], [["wrong", "a", "b"]])
    assert result["recall_at_5"] == result["recall_at_10"] == 1
    assert result["mrr_at_10"] == 0.5
    assert metrics([query], [["a"]])["recall_at_10"] == 0.5
    assert metrics([query], [["wrong"]])["failures_by_type"] == {"code": 1}


def candidates() -> list[BenchmarkResult]:
    return [
        {
            "name": p.tag,
            "digest": "a" * 64,
            "dimension": p.dimension,
            "formatting_version": p.version,
            "recall_at_5": 0.8,
            "recall_at_10": 0.9,
            "mrr_at_10": 0.7,
            "failures_by_type": {},
            "rankings": [],
            "warm_seconds_median": 0.1 + i * 0.1,
            "model_download_bytes": 100 + i,
            "benchmark_hash": "fixed",
            "errors": [],
        }
        for i, p in enumerate(PROFILES.values())
    ]


def test_winner_quality_precedes_speed_and_close_quality_prefers_speed() -> None:
    results = candidates()
    assert choose(results)["model"] == results[0]["name"]
    results[2]["recall_at_10"] = 1.0
    assert choose(results)["model"] == results[2]["name"]
    with pytest.raises(ValueError):
        choose(results[:2])
    results[1]["errors"] = ["unavailable"]
    with pytest.raises(ValueError):
        choose(results)
