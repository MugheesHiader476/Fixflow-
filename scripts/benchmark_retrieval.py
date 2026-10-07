"""Actual PostgreSQL keyword/dense retrieval over the unchanged labeled embedding corpus.

Requires a separate disposable *_test DB. Enrolls only the fixed benchmark source IDs;
never starts application workers or queries application data. No model selection.
"""

import argparse
import asyncio
import json
import math
import os
import statistics

# Fixed interpreter/migration command only.
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy import func, select

from backend.config import get_settings
from backend.db.models import DocumentChunk
from backend.db.session import close_database, get_session_factory
from backend.repositories.vectors import VectorRepository
from backend.schemas.pipeline import digest
from backend.services.embeddings import embed_source, get_embedding_provider
from backend.services.retrieval import dense_retrieve, hybrid_retrieve, keyword_retrieve, query_embedding
from backend.services.retrieval_config import retrieval_pin
from scripts.benchmark_embeddings import DATASET, OWNER, Fixture, Query, database_url, metrics, prepare

OUT = Path(".local/retrieval-benchmark")


def percentile95(samples: list[float]) -> float:
    return sorted(samples)[math.ceil(len(samples) * 0.95) - 1]


def extended_metrics(queries: list[Query], rankings: list[list[str]]) -> dict[str, object]:
    ndcg, precision5, precision10 = [], [], []
    for query, ranking in zip(queries, rankings, strict=True):
        gold = set(query["gold_chunk_ids"])
        dcg = sum(1 / math.log2(i + 2) for i, item in enumerate(ranking[:10]) if item in gold)
        ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
        ndcg.append(dcg / ideal)
        precision5.append(len(gold.intersection(ranking[:5])) / 5)
        precision10.append(len(gold.intersection(ranking[:10])) / 10)
    return {
        **metrics(queries, rankings), "ndcg_at_10": statistics.mean(ndcg),
        "precision_at_5": statistics.mean(precision5), "precision_at_10": statistics.mean(precision10),
    }


async def run(method: str, queries: list[Query], repetitions: int) -> dict[str, object]:
    ranks: list[list[str]] = []
    total, inference, search, validation, eligibility = [], [], [], [], []
    for repeat in range(repetitions):
        current = []
        for query in queries:
            async with get_session_factory()() as db:
                db.info["owner_id"] = OWNER
                start = time.perf_counter()
                if method == "keyword":
                    hits = await keyword_retrieve(db, query["text"], 10)
                    current.append([h.id for h in hits])
                    inference.append(0.0)
                    search.append(time.perf_counter() - start)
                    validation.append(0.0)
                elif method == "dense":
                    if await VectorRepository(db).has_embedding_gaps(retrieval_pin()):
                        raise ValueError("Dense benchmark corpus has embedding gaps")
                    eligibility.append(time.perf_counter() - start)
                    result = await dense_retrieve(db, query["text"], 10)
                    current.append([h.chunk_id for h in result.matches])
                    inference.append(result.embedding_seconds)
                    search.append(result.search_seconds)
                    validation.append(result.validation_seconds)
                elif method == "hybrid":
                    hybrid = await hybrid_retrieve(db, query["text"], 10)
                    current.append([h.id for h in hybrid.sources])
                    inference.append(hybrid.dense.embedding_seconds)
                    search.append(hybrid.dense.search_seconds + hybrid.keyword_seconds + hybrid.fusion_seconds)
                    validation.append(hybrid.dense.validation_seconds)
                else:
                    raise ValueError("Unknown retrieval method")
                total.append(time.perf_counter() - start)
        if repeat == 0:
            ranks = current
        elif ranks != current:
            raise ValueError("Repeated retrieval ranking changed")
    return {
        "method": method, **extended_metrics(queries, ranks), "repetitions": repetitions,
        "median_seconds": statistics.median(total), "p95_seconds": percentile95(total),
        "query_embedding_median_seconds": statistics.median(inference),
        "query_embedding_p95_seconds": percentile95(inference),
        "vector_or_keyword_search_median_seconds": statistics.median(search),
        "search_p95_seconds": percentile95(search),
        "eligibility_median_seconds": statistics.median(eligibility) if eligibility else 0,
        "validation_median_seconds": statistics.median(validation),
        "validation_p95_seconds": percentile95(validation),
        "samples_seconds": total, "errors": [],
    }


async def api_smoke(queries: list[Query]) -> dict[str, object]:
    """Actual guard/routes/store + live local inference + isolated PostgreSQL."""
    from backend.main import app  # noqa: PLC0415 - after isolated settings are loaded.

    settings = get_settings()
    if not settings.fixflow_api_token or settings.retrieval_mode != "dense":
        raise ValueError("Smoke requires a configured private gateway and dense mode")
    headers = {
        "Authorization": "Bearer " + settings.fixflow_api_token.get_secret_value(),
        "X-FixFlow-User-Id": OWNER,
    }
    outcomes = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local-test", headers=headers,
    ) as client:
        for query_id in ("q11-0", "q16-0", "project-3"):
            query = next(q for q in queries if q["id"] == query_id)
            response = await client.post("/api/debug", json={"error": query["text"]})
            if response.status_code != 200:
                raise ValueError("Live retrieval API failed")
            result = response.json()
            ids = [source["id"] for source in result["sources"]]
            rank = next((i + 1 for i, chunk_id in enumerate(ids) if chunk_id in query["gold_chunk_ids"]), None)
            if rank is None or result["generation"] != "disabled":
                raise ValueError("Live retrieval API lost gold evidence or enabled generation")
            outcomes.append({"query": query_id, "gold_rank": rank, "generation": result["generation"]})
        foreign = await client.post("/api/debug", json={"error": "source access"},
                                    headers={"X-FixFlow-User-Id": "user_foreign_smoke"})
        unauthenticated = await client.post("/api/debug", json={"error": "test"},
                                            headers={"Authorization": "Bearer invalid"})
        if foreign.status_code != 200 or foreign.json()["sources"] or unauthenticated.status_code != 401:
            raise ValueError("Live retrieval API authorization failure")
    return {
        "transport": "FastAPI ASGI -> live Ollama -> actual PostgreSQL", "queries": outcomes,
        "foreign_sources_excluded": True, "unauthenticated_rejected": True,
        "automatic_embedding_disabled": not settings.embedding_auto_process,
    }


async def execute(args: argparse.Namespace) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = cast(Fixture, json.loads(DATASET.read_text()))
    corpus, queries = await prepare(fixture)
    if queries != fixture["queries"] or len(queries) != 68 or len(corpus) != 162:
        raise ValueError("Fixed gold labels/corpus changed")
    frozen_data = {"dataset_hash": digest(fixture), "corpus": corpus, "queries": queries}
    frozen = digest(frozen_data)
    (OUT / "frozen.json").write_text(json.dumps(frozen_data, ensure_ascii=False, indent=2))
    # Pin check and artifact hashes independent of runtime/provider state.
    winner = json.loads(Path("configs/embedding-winner.json").read_text())
    if frozen != winner["benchmark_hash"]:
        raise ValueError("Benchmark content hash differs from selected embedding benchmark")
    if "hybrid" in args.methods.split(",") and not all((OUT / (m + ".json")).exists() for m in ("keyword", "dense")):
        raise ValueError("Record keyword/dense results before hybrid experiment")
    pin = retrieval_pin()
    pin.check_runtime()
    if args.persist:
        provider = get_embedding_provider()
        if provider is None:
            raise ValueError("Local Ollama unavailable")
        for source_id in dict.fromkeys(c["source_id"] for c in corpus):
            if not await embed_source(UUID(source_id), provider, OWNER):
                raise ValueError("Benchmark source failed embedding: " + source_id)
        async with get_session_factory()() as db:
            count = await db.scalar(select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.source_id.in_([UUID(c["source_id"]) for c in corpus]),
                DocumentChunk.embedding.is_not(None),
            ))
            if count != len(corpus):
                raise ValueError("Benchmark vector count mismatch")
        print(json.dumps({"persisted_vectors": count}), flush=True)
    for method in args.methods.split(","):
        if method in {"dense", "hybrid"}:
            await query_embedding(queries[0]["text"])  # Untimed warmup; measure warm retrieval consistently.
        result = await run(method, queries, args.repetitions)
        result.update({
            "benchmark_hash": frozen, "dataset_hash": digest(fixture), "queries": len(queries),
            "chunks": len(corpus), "configuration": pin.model_dump(), "stable_rankings": True,
        })
        (OUT / (method + ".json")).write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k not in {"rankings", "samples_seconds"}}), flush=True)
    if args.api_smoke:
        smoke = await api_smoke(queries)
        (OUT / "api-smoke.json").write_text(json.dumps(smoke, indent=2))
        print(json.dumps(smoke), flush=True)
    await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-smoke", action="store_true", help="Verify actual API routes with live local inference")
    parser.add_argument("--persist", action="store_true", help="Explicitly embed just the fixed benchmark corpus")
    parser.add_argument("--methods", default="keyword,dense")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 3 or any(m not in {"keyword", "dense", "hybrid"} for m in args.methods.split(",")):
        parser.error("Use keyword/dense/hybrid with at least 3 repetitions")
    url = database_url()
    os.environ["DATABASE_URL"] = url
    os.environ["EMBEDDING_AUTO_PROCESS"] = "false"
    if args.api_smoke:
        os.environ["RETRIEVAL_MODE"] = "dense"
    get_settings.cache_clear()
    # No shell, and the explicitly checked disposable DB is passed through settings.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, timeout=60)  # nosec B603
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
