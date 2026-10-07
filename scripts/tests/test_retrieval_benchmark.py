"""Fixed ranking metrics and simple RRF correctness, no model or DB needed."""

import math

import pytest

from backend.services.retrieval import reciprocal_rank_fusion
from scripts.benchmark_embeddings import Query
from scripts.benchmark_retrieval import extended_metrics, percentile95


def test_fusion_duplicate_votes_and_stable_ties() -> None:
    assert reciprocal_rank_fusion([["b", "b", "a"], ["a", "c"]], 3) == ["a", "b", "c"]
    assert reciprocal_rank_fusion([["b"], ["a"]], 2) == ["a", "b"]
    assert reciprocal_rank_fusion([[], []], 10) == []
    assert reciprocal_rank_fusion([["a", "b", "c"], []], 2) == ["a", "b"]
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], 0)


def test_ndcg_precision_and_percentile_use_fixed_gold() -> None:
    query: Query = {"id": "q", "type": "code", "text": "test", "gold_document": "d", "gold_chunk_ids": ["a", "b"]}
    correct = extended_metrics([query], [["a", "b"]])
    assert correct["ndcg_at_10"] == 1 and correct["precision_at_5"] == 0.4
    missed = extended_metrics([query], [["wrong", "a"]])
    assert missed["recall_at_10"] == 0.5
    assert missed["ndcg_at_10"] == (1 / math.log2(3)) / (1 + 1 / math.log2(3))
    assert percentile95(list(range(1, 101))) == 95
