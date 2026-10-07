# FixFlow semantic retrieval decision — 2026-10-07

**Select pinned Gemma dense retrieval with keyword fallback.** Keep untuned RRF as an isolated experiment. No chunking, selected model, generation, reranker, or UI change was made. No new migration or dependency is required.

## Fixed comparison

The unchanged corpus has 68 labeled queries, 68 sources and 162 validated chunks. Gold IDs, formatting and scoring are shared across all methods. Benchmark/dataset hashes are identical to the embedding-selection stage. All 162 vectors were generated through the real prepared-source worker in a separate disposable database, with application vectors untouched.

PostgreSQL keyword ranking retains simple full-text OR/ts_rank and its existing index. Dense uses actual pgvector cosine distance, not offline Python similarity. Hybrid uses 30 candidates per method and unweighted Reciprocal Rank Fusion with constant 60; no tuning or reranking. Final warm timing runs occurred after build/tests finished, with untimed model warmup and three repetitions (204 timed queries/method), fresh request sessions, stable rankings, and no errors. Timings include authorization/projection validation and the dense coverage guard; they are service timings, not external Clerk/browser network latency.

| Method | Recall@5 | Recall@10 | MRR@10 | NDCG@10 | Median latency | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Keyword | 75.74% | 86.03% | 0.6989 | 0.7290 | 6.1 ms | 9.4 ms |
| Dense Gemma | 94.85% | 97.06% | 0.9061 | 0.9040 | 162.4 ms | 210.7 ms |
| Hybrid RRF | 89.71% | 97.79% | 0.8193 | 0.8501 | 279.4 ms | 383.2 ms |

Dense median query inference/search/validation/coverage-check: **54.8/10.9/87.5/7.6 ms**. Component medians do not sum exactly to the median total. Per-query IDs/ranks, precision, timing samples and component p95 values are in [keyword](retrieval-results/keyword.json), [dense](retrieval-results/dense.json), and [hybrid](retrieval-results/hybrid.json).

## Failure analysis and choice

Dense improves Recall@10 by **11.03 percentage points** and recovers all eight keyword no-hit queries: documentation (1), configuration (4), multilingual (2), project code (1). Required-evidence recall wins are 9 dense versus 1 keyword; first relevant rank wins are 26 dense versus 5 keyword, with 37 ties. Neither method completely misses the same query.

Dense still misses one of two gold configuration chunks on q12-0/q12-1/q13-0/q13-1. The credential-rotation identifier query q13-1 finds both gold chunks with keyword and only one with dense. Code, tables, the identifier content-type group, continuation chunks and multilingual queries all have full dense Recall@10 in this corpus. The separately tagged exact-identifier/paraphrase analysis remains analysis-only and does not change labels.

RRF recovers one additional gold chunk for one query: only **0.74 points** more Recall@10. It loses **5.15 points** Recall@5 and **8.68 points** MRR@10, with **1.72×** median latency. Similar-but-wrong top-1 distractors: keyword 12, dense 6, hybrid 11. Fusion reinforces some wrong lexical candidates. That small completeness gain does not justify poorer near-top evidence and extra work. Full disagreements, partial misses, content/style groups and distractors are in [analysis](retrieval-results/analysis.json).

The minimum winning stack is local query embedding → authorized exact cosine search → prepared/input-identity validation, with the existing keyword engine as fallback. Ranking is imperfect, but this experiment does not justify introducing a reranker: dense already has strong required-evidence recall and better ordering than either alternative. Larger independent evaluation should precede that later experiment.

## Integration and safety

`store.py` routes existing debug/chat evidence requests through `retrieve_sources`. The pin is read from `configs/embedding-winner.json`: embeddinggemma:300m, original digest, 768 dimensions, gemma-v1, truncate=false. Query formatting is separate from canonical content. Ollama count/dimension/finite/nonzero/digest validation is reused; inference is bounded without cancelling a database transaction.

`search_pinned` filters owner, current source access/lease/account validity, eligible status, model/dimension/digest/profile/config/normalization/truncation/source hash and contract-v2 before cosine evaluation. Chunk ID breaks distance ties. Candidates then pass `prepared_source` and exact formatted document input hashes, followed by a final access/projection freshness check. Internal matches retain source IDs, raw content, full provenance/citation/version/continuation metadata and distance. Frontend relevance remains zero, avoiding invented confidence; generation remains disabled.

Keyword SQL/ranking is unchanged. A response-boundary wall-clock check prevents an expired lease from reappearing through fallback after slow inference. A partial embedding rollout uses keyword until the authorized corpus has compatible vectors, so unembedded searchable sources remain visible. Unavailable/incompatible models, missing vectors, timeout and overlong queries fall back safely.

Automatic embedding now defaults **off**. When explicitly enabled, it consumes only pending/processing jobs, never old not_configured/complete rows merely on restart. The native backend was set to dense mode with automatic embedding paused. Restart to load current configuration; enable `EMBEDDING_AUTO_PROCESS=true` only when intending to consume queued jobs. Explicit failed-source retry/source embedding remains available. General examples default to keyword; `configs/ollama.env.example` enables dense. Docker packaging includes the model pin and Compose forwards the new controls.

## Verification

- 420 Python tests passed; two native OCR tests skipped (Tesseract absent), one existing LangChain deprecation warning. The 28 targeted retrieval tests cover query dimensions/failure/timeout, owner/lease/revocation/status filters, pins, wrong-dimensional vectors, corrupted projections, no vectors, coverage gaps, metadata, RRF duplicates/ties, actual benchmark paths and debug/chat integration. Reproduced partial-corpus exclusion and expired-lease fallback failures passed after fixes.
- 118 frontend tests passed, with lint, TypeScript and production build passing. Real Next/FastAPI/PostgreSQL browser harness: 118 checks/101 fixture outcomes, 103 sources/219 documents/3,806 chunks, zero orphans. External Clerk identities were simulated; embedding transports were disabled for this separate ingestion/keyword regression. Expected validation/outage console responses are retained as evidence.
- Ruff, mypy, production Bandit, locked dependency audit and Alembic check passed. Existing migrations upgraded/downgraded in integration tests; schema check reported no operations. Compose YAML parsed; Docker image execution was not performed.
- [Live API smoke](retrieval-results/api-smoke.json): actual FastAPI guard/routes/store → live Ollama → actual PostgreSQL, three former keyword misses retrieved at ranks 5/1/1; foreign account and unauthenticated access rejected; generation disabled. This uses ASGI request transport, not live Clerk consent. [Validation](retrieval-results/validation.json) records exact gate boundaries.

## Reproduce

Use the existing native Ollama configuration and a separate disposable PostgreSQL URL ending `_test`, different from the application database. Do not share it with truncating pytest/browser runs. Run timing without concurrent tests/builds.

```bash
export EMBEDDING_BENCHMARK_DATABASE_URL='<separate disposable PostgreSQL URL ending _test>'
myenev/bin/python -m scripts.benchmark_retrieval --persist --methods keyword,dense
myenev/bin/python -m scripts.benchmark_retrieval --methods hybrid
myenev/bin/python -m scripts.benchmark_retrieval --methods keyword,dense,hybrid --api-smoke
myenev/bin/python -m scripts.retrieval_report
```

Only the deterministic benchmark source IDs are enrolled. Reuse is governed by existing exact input/model identities. The script refuses different corpus/gold hashes, mismatched configuration and embedding gaps; repeated ranks must match. No application server worker is started by this harness.

## Limits

Evidence covers this fixed, positive-query corpus on native local Ollama. Out-of-domain abstention, unseen private documents and large-corpus performance are not certified. Exact scans and request-level prepared validation will cost more as the corpus grows; no ANN/index redesign was introduced without measurements. Four dense configuration queries still have partial evidence recall. Docker host-loopback networking still requires deliberate deployment configuration; live Docker/Clerk consent was not verified. These are explicit evaluation/deployment limits, not claims of perfect semantic relevance.

Official interfaces checked: [pgvector cosine/exact search](https://github.com/pgvector/pgvector), [Ollama native embed](https://docs.ollama.com/api/embed). The measured result and chosen stack are FixFlow evidence, not an inference from those documents.
