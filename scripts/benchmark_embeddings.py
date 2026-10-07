"""Fixed prepared-source corpus, isolated PostgreSQL keyword baseline and local model evaluation.

Never reads application sources or writes vectors during candidate comparisons.
"""

import argparse
import asyncio
import json
import math
import os
import statistics

# Fixed local GPU/migration commands, no shell or document input.
import subprocess  # nosec B404
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url

from backend.config import get_settings
from backend.db.models import DocumentChunk, KnowledgeSource
from backend.db.session import close_database, get_session_factory
from backend.processing.pipeline.runner import run_pipeline
from backend.repositories.pipeline import persist_result
from backend.repositories.prepared import prepared_source
from backend.repositories.retrieval import search_chunks
from backend.schemas.pipeline import digest
from backend.services.embedding_profiles import PROFILES, FormatVersion, profile_for
from backend.services.embeddings import embed_source, get_embedding_provider
from backend.services.ollama import OllamaEmbeddingProvider


class DocumentFixture(TypedDict):
    id: str
    filename: str
    type: str
    text: str


class Query(TypedDict):
    id: str
    type: str
    text: str
    gold_document: str
    gold_contains: NotRequired[str]
    gold_chunk_ids: list[str]


class Fixture(TypedDict):
    version: int
    documents: list[DocumentFixture]
    queries: list[Query]


class CorpusChunk(TypedDict):
    chunk_id: str
    source_id: str
    document: str
    text: str
    title: str
    type: str


class Ranking(TypedDict):
    query_id: str
    first_relevant_rank: int | None
    top10: list[str]


class Quality(TypedDict):
    recall_at_5: float
    recall_at_10: float
    mrr_at_10: float
    failures_by_type: dict[str, int]
    rankings: list[Ranking]


class BenchmarkResult(Quality, total=False):
    name: str
    digest: str
    dimension: int
    formatting_version: FormatVersion
    truncate: bool
    model_download_bytes: int
    cold_seconds_median: float
    cold_seconds_first_query: float
    warm_seconds_median: float
    chunks_per_second_median: float | None
    ollama_rss_bytes_median: float
    ollama_rss_bytes_peak: int
    gpu_total_bytes_median: float
    gpu_total_bytes_peak: int
    vector_storage_bytes: int
    errors: list[str]
    repetitions: int
    cold_samples_seconds: list[float]
    ollama_version: str
    model_details: dict[str, object]
    postgres_rss_bytes_median: float
    postgres_rss_bytes_peak: int
    throughput_samples: list[float]
    benchmark_hash: str
    dataset_hash: str
    queries: int
    chunks: int
    ram: str
    vram: int


class Winner(TypedDict):
    model: str
    model_digest: str
    dimension: int
    formatting_version: FormatVersion
    truncate: bool
    winner_rule: str
    benchmark_hash: str


DATASET = Path("backend/tests/fixtures/embeddings/benchmark.json")
OWNER = "user_embedding_benchmark"
OUT = Path(".local/embedding-benchmark")
WHEN = datetime(2026, 10, 7, tzinfo=UTC)


def database_url() -> str:
    url = os.environ.get("EMBEDDING_BENCHMARK_DATABASE_URL", "")
    if not url or not (make_url(url).database or "").endswith("_test"):
        raise ValueError("Benchmark requires a separate disposable database ending _test")
    application = get_settings().sqlalchemy_url()
    if application.database == make_url(url).database:
        raise ValueError("Benchmark database must differ from application database")
    return url


def metrics(queries: list[Query], ranks: list[list[str]]) -> Quality:
    recalls5, recalls10, reciprocal = [], [], []
    failures: dict[str, int] = {}
    details: list[Ranking] = []
    for query, ranking in zip(queries, ranks, strict=True):
        gold = set(query["gold_chunk_ids"])
        recalls5.append(len(gold.intersection(ranking[:5])) / len(gold))
        recalls10.append(len(gold.intersection(ranking[:10])) / len(gold))
        rank = next((i + 1 for i, item in enumerate(ranking[:10]) if item in gold), None)
        reciprocal.append(1 / rank if rank else 0)
        if rank is None:
            failures[query["type"]] = failures.get(query["type"], 0) + 1
        details.append({"query_id": query["id"], "first_relevant_rank": rank, "top10": ranking[:10]})
    return {
        "recall_at_5": statistics.mean(recalls5),
        "recall_at_10": statistics.mean(recalls10),
        "mrr_at_10": statistics.mean(reciprocal),
        "failures_by_type": failures,
        "rankings": details,
    }


async def prepare(fixture: Fixture) -> tuple[list[CorpusChunk], list[Query]]:
    OUT.mkdir(parents=True, exist_ok=True)
    corpus: list[CorpusChunk] = []
    resolved_queries: list[Query] = []
    async with get_session_factory().begin() as db:
        db.info["owner_id"] = OWNER
        for document in fixture["documents"]:
            source_id = uuid5(NAMESPACE_URL, "fixflow-embedding-benchmark-v1:" + document["id"])
            path = OUT / "sources" / document["filename"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document["text"], encoding="utf-8")
            result = await asyncio.to_thread(
                run_pipeline,
                path,
                str(source_id),
                ingested_at=WHEN,
                source_context={"permissions": {"application_owner": OWNER, "visibility": "private"}},
            )
            source = await db.get(KnowledgeSource, source_id)
            if source is None:
                source = KnowledgeSource(
                    id=source_id,
                    owner_id=OWNER,
                    name=document["filename"],
                    source_type="docs",
                    file_hash=result.canonical.source.sha256,
                    path=str(path.resolve()),
                    status="ready_for_embedding",
                )
                db.add(source)
                await db.flush()
            elif source.file_hash != result.canonical.source.sha256:
                raise ValueError("Frozen benchmark source changed; use a new benchmark version")
            await persist_result(db, source_id, result)
            await db.flush()
            validated = await prepared_source(db, source_id, OWNER)
            if validated is None:
                raise ValueError("Benchmark source did not pass prepared handoff")
            labels = {c.concept_id: c.title for c in validated.concepts}
            corpus.extend(
                {
                    "chunk_id": chunk.chunk_id,
                    "source_id": str(source_id),
                    "document": document["id"],
                    "text": chunk.retrieval_content,
                    "title": labels[chunk.concept_id],
                    "type": document["type"],
                }
                for chunk in validated.chunks
            )
        for query in fixture["queries"]:
            gold = [
                c["chunk_id"]
                for c in corpus
                if c["document"] == query["gold_document"]
                and (not query.get("gold_contains") or query["gold_contains"] in c["text"])
            ]
            if not gold:
                raise ValueError("No gold chunks for " + query["id"])
            resolved_queries.append({**query, "gold_chunk_ids": gold})
    return corpus, resolved_queries


def resource_sample() -> tuple[int, int]:
    # Resident set of local Ollama server + runner processes; GPU total includes desktop baseline.
    rss = 0
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            try:
                command = (entry / "comm").read_text().strip()
                if (command == "ollama" and b"serve" in (entry / "cmdline").read_bytes()) or (
                    command == "llama-server" and b"/ollama/" in (entry / "cmdline").read_bytes()
                ):
                    resident_pages = int((entry / "statm").read_text().split()[1])
                    rss += resident_pages * os.sysconf("SC_PAGE_SIZE")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
    # Fixed absolute GPU tool and read-only arguments.
    gpu = subprocess.run(  # nosec B603
        ["/usr/bin/nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return rss, int(gpu.stdout.strip().splitlines()[0]) * 1024 * 1024


async def unload(http: httpx.AsyncClient, model: str) -> None:
    response = await http.post("http://127.0.0.1:11434/api/embed", json={"model": model, "input": [], "keep_alive": 0})
    response.raise_for_status()
    for _ in range(200):
        active = await http.get("http://127.0.0.1:11434/api/ps")
        active.raise_for_status()
        if not any(item["name"] == model for item in active.json()["models"]):
            return
        await asyncio.sleep(0.05)
    raise ValueError("Cannot establish model-unloaded cold state")


async def cold_samples(provider: OllamaEmbeddingProvider, query: str, repetitions: int) -> list[float]:
    samples = []
    async with httpx.AsyncClient(timeout=120, trust_env=False) as http:
        for _ in range(repetitions):
            await unload(http, provider.model)
            started = time.perf_counter()
            await provider.embed([profile_for(provider.model).query(query)])
            samples.append(time.perf_counter() - started)
    return samples


async def benchmark_model(
    tag: str, corpus: list[CorpusChunk], queries: list[Query], repetitions: int
) -> BenchmarkResult:
    profile = profile_for(tag)
    async with httpx.AsyncClient(timeout=120, trust_env=False) as http:
        ollama_version = (await http.get("http://127.0.0.1:11434/api/version")).json()["version"]
        tags = (await http.get("http://127.0.0.1:11434/api/tags")).json()["models"]
        model = next(item for item in tags if item["name"] == tag)
        # Unload every previous model to measure a comparable cold start.
        for item in tags:
            await unload(http, item["name"])
        provider = OllamaEmbeddingProvider("http://127.0.0.1:11434", tag, profile.dimension, model["digest"])
        samples: list[tuple[int, int]] = []
        monitoring = True

        async def monitor() -> None:
            while monitoring:
                samples.append(await asyncio.to_thread(resource_sample))
                await asyncio.sleep(0.5)

        monitor_task = asyncio.create_task(monitor())
        try:
            cold = await cold_samples(provider, queries[0]["text"], repetitions)
            texts = [profile.document(c["text"], c["title"]) for c in corpus]
            throughput = []
            vectors: list[list[float]] = []
            for repeat in range(repetitions):
                current = []
                started = time.perf_counter()
                for start in range(0, len(texts), 4):
                    current.extend(await provider.embed(texts[start : start + 4]))
                throughput.append(len(texts) / (time.perf_counter() - started))
                if repeat == 0:
                    vectors = current
            unit_vectors = [[x / math.hypot(*v) for x in v] for v in vectors]
            latencies = []
            rankings = []
            for repeat in range(repetitions):
                current_ranks = []
                for query in queries:
                    started = time.perf_counter()
                    vector = (await provider.embed([profile.query(query["text"])]))[0]
                    norm = math.hypot(*vector)
                    # Offline exact cosine; not a product retrieval path or approximate index.
                    scores = [sum(a * b for a, b in zip(vector, v, strict=True)) / norm for v in unit_vectors]
                    indexes = sorted(range(len(corpus)), key=lambda i: (-scores[i], corpus[i]["chunk_id"]))[:10]
                    current_ranks.append([corpus[i]["chunk_id"] for i in indexes])
                    latencies.append(time.perf_counter() - started)
                if repeat == 0:
                    rankings = current_ranks
                elif current_ranks != rankings:
                    raise ValueError("Repeated model ranking changed")
            result: BenchmarkResult = {
                "name": tag,
                "ollama_version": ollama_version,
                "model_details": model["details"],
                "digest": model["digest"],
                "dimension": profile.dimension,
                "formatting_version": profile.version,
                "truncate": False,
                "model_download_bytes": model["size"],
                **metrics(queries, rankings),
                "cold_seconds_median": statistics.median(cold),
                "warm_seconds_median": statistics.median(latencies),
                "chunks_per_second_median": statistics.median(throughput),
                "ollama_rss_bytes_median": statistics.median(s[0] for s in samples),
                "ollama_rss_bytes_peak": max(s[0] for s in samples),
                "gpu_total_bytes_median": statistics.median(s[1] for s in samples),
                "gpu_total_bytes_peak": max(s[1] for s in samples),
                "vector_storage_bytes": len(vectors) * (4 * profile.dimension + 8),
                "errors": [],
                "repetitions": repetitions,
                "cold_samples_seconds": cold,
                "throughput_samples": throughput,
            }
            (OUT / (tag.replace(":", "-") + "-vectors.json")).write_text(json.dumps(vectors))
            return result
        finally:
            monitoring = False
            await monitor_task


def postgres_rss() -> int:
    total = 0
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            try:
                if (entry / "comm").read_text().strip() == "postgres":
                    total += int((entry / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
    return total


async def baseline(corpus: list[CorpusChunk], queries: list[Query], repetitions: int) -> BenchmarkResult:
    cold = []
    for _ in range(repetitions):
        await close_database()
        start = time.perf_counter()
        async with get_session_factory()() as db:
            db.info["owner_id"] = OWNER
            await search_chunks(db, queries[0]["text"], limit=10)
        cold.append(time.perf_counter() - start)
    latencies: list[float] = []
    rankings: list[list[str]] = []
    memory = []
    async with get_session_factory()() as db:
        db.info["owner_id"] = OWNER
        for repeat in range(repetitions):
            current = []
            for query in queries:
                start = time.perf_counter()
                rows = await search_chunks(db, query["text"], limit=10)
                latencies.append(time.perf_counter() - start)
                current.append([row.id for row in rows])
            memory.append(await asyncio.to_thread(postgres_rss))
            if repeat == 0:
                rankings = current
            elif rankings != current:
                raise ValueError("Keyword baseline changed")
    return {
        "name": "postgresql-keyword",
        **metrics(queries, rankings),
        "cold_seconds_median": statistics.median(cold),
        "cold_samples_seconds": cold,
        "warm_seconds_median": statistics.median(latencies),
        "dimension": 0,
        "vector_storage_bytes": 0,
        "errors": [],
        "repetitions": repetitions,
        "chunks_per_second_median": None,
        "postgres_rss_bytes_median": statistics.median(memory),
        "postgres_rss_bytes_peak": max(memory),
        "ram": "aggregate PostgreSQL RSS includes shared pages and other databases; not isolated",
        "vram": 0,
    }


def choose(results: list[BenchmarkResult]) -> Winner:
    if len(results) != len(PROFILES) or {r["name"] for r in results} != set(PROFILES):
        raise ValueError("All initial candidate results are required before choosing")
    if any(r["errors"] for r in results):
        raise ValueError("Candidate execution failure blocks selection")
    best = max(r["recall_at_10"] for r in results)
    close = [r for r in results if r["recall_at_10"] >= best - 0.01]
    best5 = max(r["recall_at_5"] for r in close)
    close = [r for r in close if r["recall_at_5"] >= best5 - 0.01]
    best_mrr = max(r["mrr_at_10"] for r in close)
    close = [r for r in close if r["mrr_at_10"] >= best_mrr - 0.01]
    winner = min(close, key=lambda r: (r["warm_seconds_median"], r["model_download_bytes"]))
    return {
        "model": winner["name"],
        "model_digest": winner["digest"],
        "dimension": winner["dimension"],
        "formatting_version": winner["formatting_version"],
        "truncate": False,
        "winner_rule": (
            "Recall@10 then Recall@5 then MRR@10; within 0.01 at each gate "
            "prefer lower warm latency, then download size"
        ),
        "benchmark_hash": winner["benchmark_hash"],
    }


async def execute(args: argparse.Namespace) -> None:
    fixture = cast(Fixture, json.loads(DATASET.read_text()))
    corpus, queries = await prepare(fixture)
    frozen = {"dataset_hash": digest(fixture), "corpus": corpus, "queries": queries}
    frozen_hash = digest(frozen)
    # Gold chunk IDs are checked into the fixture, not selected from any model output.
    if any(q.get("gold_chunk_ids") != r["gold_chunk_ids"] for q, r in zip(fixture["queries"], queries, strict=True)):
        raise ValueError("Frozen gold chunk IDs changed")
    (OUT / "frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2))
    if args.refresh_cold:
        if not args.model:
            raise ValueError("Cold measurement requires model")
        path = OUT / (args.model.replace(":", "-") + ".json")
        existing = cast(BenchmarkResult, json.loads(path.read_text()))
        if existing["benchmark_hash"] != frozen_hash:
            raise ValueError("Cold refresh corpus changed")
        provider = OllamaEmbeddingProvider(
            "http://127.0.0.1:11434", args.model, existing["dimension"], existing["digest"]
        )
        samples = await cold_samples(provider, queries[0]["text"], args.repetitions)
        existing["cold_samples_seconds"] = samples
        existing["cold_seconds_median"] = statistics.median(samples)
        path.write_text(json.dumps(existing, indent=2))
        print(json.dumps({"model": args.model, "verified_unloaded_cold_samples": samples}))
    elif args.select:
        results = [
            cast(BenchmarkResult, json.loads((OUT / (tag.replace(":", "-") + ".json")).read_text())) for tag in PROFILES
        ]
        keyword = cast(BenchmarkResult, json.loads((OUT / "keyword.json").read_text()))
        if any(r["benchmark_hash"] != frozen_hash for r in [*results, keyword]):
            raise ValueError("Model comparisons used different corpus or labels")
        winner = choose(results)
        (OUT / "winner.json").write_text(json.dumps(winner, indent=2))
        print(json.dumps(winner, indent=2))
    elif args.persist:
        winner = cast(Winner, json.loads((OUT / "winner.json").read_text()))
        if winner["benchmark_hash"] != frozen_hash:
            raise ValueError("Selected winner does not match this benchmark")
        settings = get_settings()
        settings.ollama_url = "http://127.0.0.1:11434"
        settings.embedding_model = winner["model"]
        settings.embedding_dim = winner["dimension"]
        settings.embedding_model_digest = winner["model_digest"]
        settings.embedding_profile = winner["formatting_version"]
        settings.embedding_batch_size = 4
        settings.embedding_workers = 1
        winning_provider = get_embedding_provider()
        if winning_provider is None:
            raise ValueError("Winning provider is not configured")
        source_id = UUID(args.persist)
        if not await embed_source(source_id, winning_provider, OWNER):
            raise ValueError("Winner source persistence failed")
        async with get_session_factory()() as db:
            validated = await prepared_source(db, source_id, OWNER)
            records = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.source_id == source_id)))
            if validated is None or len(validated.chunks) != len(records):
                raise ValueError("Incomplete persisted source")
            if not all(c.embedding is not None and len(c.embedding) == winner["dimension"] for c in records):
                raise ValueError("Persisted vector dimensions/count invalid")
            if not all(
                isinstance(c.meta["embedding_identity"], dict)
                and c.meta["embedding_identity"].get("model_digest") == winner["model_digest"]
                for c in records
            ):
                raise ValueError("Persisted vector identity invalid")
            db.info["owner_id"] = OWNER
            if not await search_chunks(db, "CONT_AUTH_701", limit=10):
                raise ValueError("Keyword persistence regression")
            persisted_result = {
                "source_id": str(source_id),
                "chunks": len(records),
                "embedded": len(records),
                "prepared_validated": True,
                "keyword_operational": True,
                "configuration": winner,
            }
            (OUT / "persistence.json").write_text(json.dumps(persisted_result, indent=2))
            print(json.dumps(persisted_result, indent=2))
    else:
        result = (
            await benchmark_model(args.model, corpus, queries, args.repetitions)
            if args.model
            else await baseline(corpus, queries, args.repetitions)
        )
        result.update(
            {
                "benchmark_hash": frozen_hash,
                "dataset_hash": digest(fixture),
                "queries": len(queries),
                "chunks": len(corpus),
            }
        )
        name = args.model.replace(":", "-") if args.model else "keyword"
        (OUT / (name + ".json")).write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "rankings"}, indent=2))
    await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=list(PROFILES))
    parser.add_argument("--refresh-cold", action="store_true", help="Refresh cold samples with verified unloading")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--persist", help="One benchmark source UUID after winner selection")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.refresh_cold and not args.model:
        parser.error("Cold refresh requires --model")
    if args.repetitions < 3:
        parser.error("At least three repetitions required")
    url = database_url()
    os.environ["DATABASE_URL"] = url
    os.environ["OLLAMA_URL"] = ""
    os.environ["EMBEDDING_API_URL"] = ""
    get_settings.cache_clear()
    # Current Python interpreter and fixed migration arguments.
    subprocess.run(  # nosec B603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        timeout=60,
    )
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
