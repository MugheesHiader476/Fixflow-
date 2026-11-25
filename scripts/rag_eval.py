"""Golden-set RAG evaluation: retrieval grid (free, local) and end-to-end chat (Groq, bounded).

Runs only against a disposable database whose name ends in `_test` (set DATABASE_URL). It loads the
golden corpus from backend/tests/golden_set.py for a dedicated owner, parses it with the real
pipeline, indexes and embeds it with the configured local model, and scores the exact search path
chat uses (`workspace_tools.snapshots` → model-facing `model_output`).

  python -m scripts.rag_eval retrieval --out .local/rag-eval
  python -m scripts.rag_eval e2e --out .local/rag-eval --split test [--config '{"target": 450}']
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url

from backend.config import get_settings
from backend.db.models import (
    AgentRun,
    ConnectorAccount,
    ConnectorRateLimit,
    EmbeddingJob,
    EvidencePassage,
    KnowledgeSource,
    PassageIndex,
)
from backend.db.session import close_database, get_session_factory
from backend.processing import passages as passages_module
from backend.processing.embedding_input import profile
from backend.processing.passages import tokens
from backend.repositories import evidence as evidence_repository
from backend.repositories.parsed import parsed_source
from backend.schemas.agent import RunRequest, SearchSnapshots
from backend.schemas.evidence import IndexRequest
from backend.services import embeddings, workspace_tools
from backend.services.agent import create, model_output, process_step, view
from backend.services.ingestion import ingest_source
from backend.tests.golden_set import DOCS, QUESTIONS, Question, load_corpus

OWNER = "user_golden_eval"


@dataclass(frozen=True)
class Config:
    target: int = 450
    soft_max: int = 650
    overlap: int = 40
    max_passages: int = 5
    context_tokens: int = 2200
    candidate_limit: int = 24
    hybrid: bool = True
    min_similarity: float = 0.5

    def label(self) -> str:
        return (
            f"t{self.target}/s{self.soft_max}/o{self.overlap} k{self.max_passages}/c{self.context_tokens}"
            f"/n{self.candidate_limit} {'hybrid' if self.hybrid else 'lexical'}@{self.min_similarity}"
        )


def guard_database() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url or not (make_url(url).database or "").endswith("_test"):
        raise SystemExit("Set DATABASE_URL to a disposable database whose name ends in _test")


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


async def reset_owner() -> None:
    async with get_session_factory().begin() as db:
        await db.execute(delete(AgentRun).where(AgentRun.owner_id == OWNER))
        await db.execute(delete(KnowledgeSource).where(KnowledgeSource.owner_id == OWNER))
        await db.execute(delete(ConnectorAccount).where(ConnectorAccount.owner_id == OWNER))


async def load() -> list[UUID]:
    await reset_owner()
    async with get_session_factory().begin() as db:
        identifiers = await load_corpus(db, OWNER)
    failures = []
    for identifier in identifiers:
        try:
            await ingest_source(identifier)
        except Exception as error:  # noqa: BLE001 - reported, never hidden
            failures.append(f"{identifier}: {type(error).__name__}")
    async with get_session_factory()() as db:
        statuses = {
            row.id: (row.status, row.error_message)
            for row in await db.scalars(select(KnowledgeSource).where(KnowledgeSource.owner_id == OWNER))
        }
    bad = {str(k): v for k, v in statuses.items() if v[0] != "parsed"}
    if failures or bad:
        print("PARSE_FAILURES", failures, bad)
    return identifiers


def apply(config: Config) -> None:
    """Set chunking and search knobs in-process; the version suffix forces a clean rebuild per setting."""
    version = f"eval-{config.target}-{config.soft_max}-{config.overlap}-cl100k_base"
    passages_module.TARGET_TOKENS = config.target
    passages_module.SOFT_MAX_TOKENS = config.soft_max
    passages_module.LONG_PROSE_OVERLAP = config.overlap
    passages_module.PASSAGE_VERSION = version
    evidence_repository.PASSAGE_VERSION = version
    workspace_tools.SNAPSHOT_MAX_PASSAGES = config.max_passages
    workspace_tools.SNAPSHOT_CONTEXT_TOKENS = config.context_tokens
    workspace_tools.SNAPSHOT_CANDIDATE_LIMIT = config.candidate_limit
    os.environ["EMBEDDINGS__MIN_SIMILARITY"] = str(config.min_similarity)
    if not config.hybrid:
        os.environ["EMBEDDINGS__URL"] = ""
    else:
        os.environ["EMBEDDINGS__URL"] = os.environ.get("EVAL_EMBEDDINGS_URL", "http://127.0.0.1:11435")
    get_settings.cache_clear()


async def reindex(identifiers: list[UUID], *, embed: bool) -> dict[str, object]:
    """Rebuild passages for the current chunk setting and embed them; returns index statistics."""
    counts, sizes, skipped = [], [], []
    for identifier in identifiers:
        async with get_session_factory().begin() as db:
            db.info["owner_id"] = OWNER
            source = await db.get(KnowledgeSource, identifier)
            result = await parsed_source(db, identifier, OWNER)
            if source is None or result is None:
                skipped.append(str(identifier))
                continue
            try:
                async with db.begin_nested():
                    count = await evidence_repository.write_index(
                        db, source, result, IndexRequest(project=evidence_repository.AUTOMATIC_PROJECT, force=True)
                    )
            except ValueError:
                skipped.append(source.name)
                await db.execute(delete(PassageIndex).where(PassageIndex.source_id == identifier))
                continue
            counts.append(count)
            if embed:
                await evidence_repository.queue_embedding(db, identifier, profile(get_settings().embeddings.revision))
    if embed:
        for identifier in identifiers:
            for _ in range(50):
                async with get_session_factory()() as db:
                    job = await db.get(EmbeddingJob, identifier)
                if job is None or job.status in {"complete", "failed", "cancelled"}:
                    break
                # Local model: skip the worker's inter-batch pause and per-source pacing for the evaluation owner.
                async with get_session_factory().begin() as db:
                    await db.execute(
                        update(EmbeddingJob)
                        .where(EmbeddingJob.source_id == identifier)
                        .values(next_attempt_at=func.now() - text("interval '1 second'"))
                    )
                    await db.execute(
                        update(ConnectorRateLimit)
                        .where(ConnectorRateLimit.key.like("evidence-embed%"))
                        .values(next_allowed_at=func.now() - text("interval '1 second'"))
                    )
                await embeddings.process_batch(identifier)
    async with get_session_factory()() as db:
        rows = await db.scalars(
            select(PassageIndex.source_id).where(PassageIndex.source_id.in_(identifiers))
        )
        indexed = set(rows)
        sizes = [
            tokens(row.prefix + "\n\n" + row.content)
            for row in await db.scalars(select(EvidencePassage).where(EvidencePassage.source_id.in_(indexed)))
        ]
        failed_jobs = [
            job.error
            for job in await db.scalars(select(EmbeddingJob).where(EmbeddingJob.source_id.in_(indexed)))
            if job.status != "complete"
        ]
    return {
        "passages": len(sizes),
        "mean_passage_tokens": round(statistics.mean(sizes), 1) if sizes else 0,
        "max_passage_tokens": max(sizes, default=0),
        "unindexable_sources": skipped,
        "embedding_failures": failed_jobs if embed else [],
    }


async def context() -> workspace_tools.Context:
    async with get_session_factory()() as db:
        accounts = list(await db.scalars(select(ConnectorAccount).where(ConnectorAccount.owner_id == OWNER)))
    scope: dict[str, object] = {"accounts": [{"id": str(a.id), "provider": a.provider} for a in accounts]}
    return workspace_tools.Context(OWNER, uuid4(), RunRequest(question="golden evaluation"), scope, [])


async def score_query(ctx: workspace_tools.Context, question: Question, query: str) -> dict[str, object]:
    started = time.perf_counter()
    result = await workspace_tools.snapshots(ctx, SearchSnapshots(query=query))
    elapsed = (time.perf_counter() - started) * 1000
    view = model_output(result)
    texts = [compact(str(item.get("text", ""))) for item in cast(list[dict[str, object]], view["materials"])]
    found = [any(compact(gold) in text for text in texts) for gold in question.gold]
    first = next((i + 1 for i, text in enumerate(texts) if any(compact(g) in text for g in question.gold)), None)
    return {
        "hit": bool(question.gold) and all(found),
        "partial": sum(found) / len(found) if found else None,
        "rr": (1 / first) if first else 0.0,
        "materials": len(texts),
        "evidence_tokens": tokens(json.dumps(view, ensure_ascii=False)),
        "embedding_calls": result.get("embedding_calls", 0),
        "ms": round(elapsed, 1),
    }


def summarize(rows: list[dict[str, object]], split: str | None = None) -> dict[str, object]:
    selected = [r for r in rows if split is None or r["split"] == split]
    answerable = [r for r in selected if r["category"] != "unanswerable"]
    by_category: dict[str, list[dict[str, object]]] = {}
    for row in answerable:
        by_category.setdefault(str(row["category"]), []).append(row)

    def rate(items: list[dict[str, object]], key: str) -> float:
        return round(sum(float(cast(float, r[key])) for r in items) / len(items), 3) if items else 0.0

    return {
        "questions": len(answerable),
        "hit_rate": rate(answerable, "hit"),
        "mrr": rate(answerable, "rr"),
        "mean_evidence_tokens": round(statistics.mean(float(cast(int, r["evidence_tokens"])) for r in selected), 1),
        "p95_evidence_tokens": sorted(cast(int, r["evidence_tokens"]) for r in selected)[
            max(0, int(len(selected) * 0.95) - 1)
        ],
        "mean_materials": rate(selected, "materials"),
        "unanswerable_mean_materials": rate([r for r in selected if r["category"] == "unanswerable"], "materials"),
        "p50_ms": statistics.median(float(cast(float, r["ms"])) for r in selected),
        "by_category": {k: rate(v, "hit") for k, v in sorted(by_category.items())},
    }


async def evaluate(config: Config, identifiers: list[UUID], *, rebuild: bool) -> dict[str, object]:
    apply(config)
    stats = await reindex(identifiers, embed=config.hybrid) if rebuild else {}
    ctx = await context()
    results: dict[str, list[dict[str, object]]] = {"question": [], "keywords": []}
    for question in QUESTIONS:
        for style, query in (("question", question.question), ("keywords", question.keywords)):
            row = await score_query(ctx, question, query)
            results[style].append({"key": question.key, "split": question.split, "category": question.category, **row})
    return {
        "config": asdict(config),
        "label": config.label(),
        "index": stats,
        **{
            style: {"dev": summarize(rows, "dev"), "test": summarize(rows, "test"), "all": summarize(rows)}
            for style, rows in results.items()
        },
        "misses": {
            style: [r["key"] for r in rows if r["category"] != "unanswerable" and not r["hit"]]
            for style, rows in results.items()
        },
    }


def objective(report: dict[str, object], split: str = "dev") -> float:
    """Recall under the free-tier evidence budget: both query styles, penalised above ~1.8K tokens."""
    score = 0.0
    for style in ("keywords", "question"):
        part = cast(dict[str, dict[str, float]], report[style])[split]
        penalty = max(0.0, (part["p95_evidence_tokens"] - 1800) / 1000)
        score += part["hit_rate"] + 0.5 * part["mrr"] - penalty
    return round(score / 2, 4)


CONFIGS: dict[str, Config] = {}


def best_config(reports: list[dict[str, object]]) -> Config:
    best = max(reports, key=lambda report: cast(float, report["objective_dev"]))
    return CONFIGS[str(best["label"])]


async def retrieval(out: Path, chunk: str | None = None) -> None:
    identifiers = await load()
    print(f"Loaded {len(DOCS)} documents, {len(QUESTIONS)} questions")
    reports: list[dict[str, object]] = []
    baseline = Config()

    async def run(config: Config, rebuild: bool) -> dict[str, object]:
        CONFIGS[config.label()] = config
        report = await evaluate(config, identifiers, rebuild=rebuild)
        report["objective_dev"] = objective(report, "dev")
        reports.append(report)
        k = cast(dict[str, dict[str, object]], report["keywords"])
        q = cast(dict[str, dict[str, object]], report["question"])
        print(
            f"{config.label():58} dev obj={report['objective_dev']:.3f} | kw hit={k['dev']['hit_rate']} "
            f"q hit={q['dev']['hit_rate']} | p95 tok={k['dev']['p95_evidence_tokens']} "
            f"| passages={cast(dict[str, object], report['index']).get('passages', '-')}",
            flush=True,
        )
        return report

    # Stage 1: chunk size and retrieval mode (rebuild + re-embed per chunk setting).
    # `--chunk target,soft_max,overlap` resumes from a known stage-1 winner instead.
    if chunk:
        target, soft, overlap = (int(part) for part in chunk.split(","))
        best_chunk = Config(target=target, soft_max=soft, overlap=overlap)
        await run(best_chunk, rebuild=True)
    else:
        stage1 = []
        for target, soft in ((250, 400), (350, 500), (450, 650), (600, 850)):
            for overlap in (0, 40):
                config = Config(target=target, soft_max=soft, overlap=overlap)
                stage1.append(await run(config, rebuild=True))
                stage1.append(await run(Config(**{**asdict(config), "hybrid": False}), rebuild=False))
        best_chunk = best_config(stage1)
    # Stage 2: similarity cutoff (only meaningful for hybrid).
    if best_chunk.hybrid:
        apply(best_chunk)
        await reindex(identifiers, embed=True)
        stage2 = [await run(Config(**{**asdict(best_chunk), "min_similarity": s}), rebuild=False)
                  for s in (0.3, 0.4, 0.45, 0.5, 0.55, 0.6)]
        best_chunk = best_config(stage2)
    # Stage 3: evidence budget per search (passages, tokens, candidates).
    stage3 = [
        await run(Config(**{**asdict(best_chunk), "max_passages": k, "context_tokens": c}), rebuild=False)
        for k, c in ((3, 1500), (4, 1500), (4, 1800), (5, 1800), (5, 2200), (6, 2200), (6, 3000))
    ]
    # A wider candidate pool only for the best budget (rank fusion sees more lexical candidates).
    widest = best_config(stage3)
    stage3.append(await run(Config(**{**asdict(widest), "candidate_limit": 48}), rebuild=False))
    # Held-out comparison: baseline vs chosen, rebuilt from scratch.
    final_baseline = await evaluate(baseline, identifiers, rebuild=True)
    chosen = best_config(stage3)
    final_chosen = await evaluate(chosen, identifiers, rebuild=True)
    summary = {
        "corpus": {"documents": len(DOCS), "questions": len(QUESTIONS)},
        "baseline": final_baseline,
        "chosen": final_chosen,
        "grid": [{k: v for k, v in r.items() if k in {"label", "objective_dev", "index"}} for r in reports],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "retrieval.json").write_text(json.dumps(summary, indent=2, default=str))
    print("\nHELD-OUT (test split)")
    for name, report in (("baseline", final_baseline), ("chosen", final_chosen)):
        for style in ("keywords", "question"):
            part = cast(dict[str, dict[str, object]], report[style])["test"]
            print(f"  {name:8} {style:8} {report['label']:58} hit={part['hit_rate']} mrr={part['mrr']} "
                  f"p95_tok={part['p95_evidence_tokens']} by_cat={part['by_category']}")
    print("Saved", out / "retrieval.json")


async def e2e(out: Path, split: str, config: Config, limit: int | None) -> None:
    """Real chat runs through the worker loop (Groq). Bounded: one question at a time, sequential."""
    identifiers = await load()
    apply(config)
    await reindex(identifiers, embed=config.hybrid)
    selected = [q for q in QUESTIONS if q.split == split][: limit or None]
    rows = []
    for question in selected:
        async with get_session_factory()() as db:
            db.info["owner_id"] = OWNER
            run = await create(db, RunRequest(question=question.question))
            identifier = run.id
        started = time.perf_counter()
        status = "queued"
        final = None
        while time.perf_counter() - started < get_settings().grok.run_timeout_seconds + 60:
            await process_step(identifier)
            async with get_session_factory()() as db:
                current = await db.get(AgentRun, identifier, populate_existing=True)
                if current is None:
                    raise RuntimeError("Evaluation run disappeared")
                run = current
                status = run.status
                if status not in {"queued", "running"}:
                    db.info["owner_id"] = OWNER
                    final = await view(db, run)
                    break
                wait = max(0.0, (run.next_attempt_at.timestamp() - time.time()))
            await asyncio.sleep(min(30.0, max(0.2, wait)))
        answer = final.answer if final else None
        text = compact(" ".join([answer.answer, *(c.text for c in answer.claims)])) if answer else ""
        abstained = answer is not None and (not answer.claims or not question.gold)
        correct = (
            all(compact(fact) in text for fact in question.answer)
            if question.gold
            else (answer is not None and not answer.claims)
        )
        rows.append({
            "key": question.key,
            "category": question.category,
            "status": status,
            "correct": correct,
            "abstained_when_unanswerable": abstained if not question.gold else None,
            "steps": run.steps,
            "input_tokens": run.usage.get("input_tokens"),
            "output_tokens": run.usage.get("output_tokens"),
            "seconds": round(time.perf_counter() - started, 1),
            "error": run.error,
            "answer": answer.answer if answer else None,
        })
        print(json.dumps(rows[-1], default=str), flush=True)
    finished = [r for r in rows if r["status"] == "completed"]
    summary = {
        "config": asdict(config),
        "model": get_settings().grok.model,
        "questions": len(rows),
        "completed_rate": round(len(finished) / len(rows), 3) if rows else 0,
        "accuracy": round(sum(bool(r["correct"]) for r in rows) / len(rows), 3) if rows else 0,
        "mean_input_tokens": mean(finished, "input_tokens"),
        "mean_output_tokens": mean(finished, "output_tokens"),
        "mean_steps": mean(rows, "steps"),
        "median_seconds": statistics.median(cast(float, r["seconds"]) for r in rows) if rows else None,
        "rows": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    name = f"e2e-{split}-{config.label().replace('/', '_').replace(' ', '_').replace('@', '_')}.json"
    (out / name).write_text(json.dumps(summary, indent=2, default=str))
    print({k: v for k, v in summary.items() if k != "rows"})
    print("Saved", out / name)


def mean(rows: list[dict[str, object]], key: str) -> float | None:
    values = [float(cast(float, row[key])) for row in rows if row.get(key) is not None]
    return round(statistics.mean(values), 1) if values else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["retrieval", "e2e"])
    parser.add_argument("--out", type=Path, default=Path(".local/rag-eval"))
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--config", default="{}", help="JSON overrides for Config (e2e)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--chunk", default=None, help="Resume retrieval at stage 2: target,soft_max,overlap")
    args = parser.parse_args()
    guard_database()
    os.environ.setdefault("EMBEDDINGS__REQUESTS_PER_MINUTE", "600")
    os.environ.setdefault("EMBEDDINGS__AUTO_EMBED", "false")
    os.environ.setdefault("EVAL_EMBEDDINGS_URL", os.environ.get("EMBEDDINGS__URL") or "http://127.0.0.1:11435")
    get_settings.cache_clear()

    async def runner() -> None:
        try:
            if args.mode == "retrieval":
                await retrieval(args.out, args.chunk)
            else:
                await e2e(args.out, args.split, Config(**json.loads(args.config)), args.limit)
        finally:
            await close_database()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
