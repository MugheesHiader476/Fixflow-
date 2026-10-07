"""Reproducible failure analysis of the three fixed retrieval reports."""

import json
from collections import Counter
from pathlib import Path
from typing import cast

from backend.schemas.pipeline import digest
from scripts.benchmark_embeddings import DATASET, Fixture, Quality, Ranking


class Report(Quality):
    benchmark_hash: str
    dataset_hash: str


def analyze(directory: Path) -> dict[str, object]:
    fixture = cast(Fixture, json.loads(DATASET.read_text()))
    reports = {name: cast(Report, json.loads((directory / (name + ".json")).read_text()))
               for name in ("keyword", "dense", "hybrid")}
    if len({r["benchmark_hash"] for r in reports.values()}) != 1:
        raise ValueError("Compared reports have different corpora")
    if len({r["dataset_hash"] for r in reports.values()}) != 1:
        raise ValueError("Compared reports have different queries")
    rankings: dict[str, dict[str, Ranking]] = {
        name: {r["query_id"]: r for r in report["rankings"]} for name, report in reports.items()
    }
    keyword_wins, dense_wins, both_fail, partial, different = [], [], [], [], []
    by_type: dict[str, dict[str, int]] = {}
    for query in fixture["queries"]:
        gold = set(query["gold_chunk_ids"])
        hits = {name: gold.intersection(ranks[query["id"]]["top10"]) for name, ranks in rankings.items()}
        detail = {
            "query_id": query["id"], "type": query["type"], "query": query["text"], "gold": sorted(gold),
            "hit_chunks": {name: sorted(ids) for name, ids in hits.items()},
            "first_relevant_rank": {
                name: ranks[query["id"]]["first_relevant_rank"] for name, ranks in rankings.items()
            },
        }
        if len(hits["keyword"]) > len(hits["dense"]):
            keyword_wins.append(detail)
        if len(hits["dense"]) > len(hits["keyword"]):
            dense_wins.append(detail)
        if not hits["keyword"] and not hits["dense"]:
            both_fail.append(detail)
        if hits["keyword"] != hits["dense"] and hits["keyword"] and hits["dense"]:
            different.append(detail)
        if any(ids != gold for ids in hits.values()):
            partial.append(detail)
        bucket = by_type.setdefault(query["type"], {"queries": 0})
        bucket["queries"] += 1
        for name, ids in hits.items():
            bucket[name + "_missing_gold_chunks"] = bucket.get(name + "_missing_gold_chunks", 0) + len(gold - ids)
            if not ids:
                bucket[name + "_no_hit_queries"] = bucket.get(name + "_no_hit_queries", 0) + 1
    # Exact/semantic style tags are analysis-only; immutable gold labels never change.
    exact_ids = {"q00-1", "q01-1", "q02-1", "q03-1", "q04-1", "q05-1", "q06-1", "q08-1", "q09-1",
                 "q13-1", "q15-1", "q18-1", "q20-1", "q23-1", "q24-0", "q25-0", "q26-1", "project-1"}
    paraphrases = {
        q["id"] for q in fixture["queries"] if q["id"].startswith("q") and q["id"].endswith("-0")
    } - exact_ids
    styles = {}
    for label, ids in (("exact_identifiers", exact_ids), ("paraphrases", paraphrases)):
        styles[label] = {
            "query_ids": sorted(ids),
            "missing_gold_chunks": {
                name: sum(len(set(q["gold_chunk_ids"]) - set(ranks[q["id"]]["top10"]))
                          for q in fixture["queries"] if q["id"] in ids)
                for name, ranks in rankings.items()
            },
        }
    # Report candidate disagreements/ranking wins separately from required-evidence recall wins.
    rank_wins: Counter[str] = Counter()
    for query in fixture["queries"]:
        kr = rankings["keyword"][query["id"]]["first_relevant_rank"] or 11
        dr = rankings["dense"][query["id"]]["first_relevant_rank"] or 11
        rank_wins["keyword" if kr < dr else "dense" if dr < kr else "tie"] += 1
    frozen = json.loads((directory / "frozen.json").read_text())
    if digest(frozen) != reports["dense"]["benchmark_hash"]:
        raise ValueError("Failure analysis corpus mismatch")
    distractor_ids = {c["chunk_id"] for c in frozen["corpus"] if c["type"] == "distractor"}
    distractors = {
        name: {
            "top1_wrong_distractor": sum(bool(r["top10"]) and r["top10"][0] in distractor_ids for r in ranks.values()),
            "total_top10_distractors": sum(len(set(r["top10"]) & distractor_ids) for r in ranks.values()),
        }
        for name, ranks in rankings.items()
    }
    return {
        "distractors": distractors,
        "by_content_type": by_type, "analysis_only_style_tags": styles,
        "keyword_recall_wins": keyword_wins, "dense_recall_wins": dense_wins,
        "both_no_hit": both_fail, "partial_evidence_misses": partial,
        "different_useful_chunks": different, "first_relevant_ranking_wins": dict(rank_wins),
    }


if __name__ == "__main__":
    result = analyze(Path(".local/retrieval-benchmark"))
    Path(".local/retrieval-benchmark/analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({"keyword_recall_wins": len(cast(list[object], result["keyword_recall_wins"])),
                      "dense_recall_wins": len(cast(list[object], result["dense_recall_wins"])),
                      "both_no_hit": len(cast(list[object], result["both_no_hit"]))}))
