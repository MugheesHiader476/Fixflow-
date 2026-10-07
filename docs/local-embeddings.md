# Local embedding release — 2026-10-07

FixFlow now supports native local Ollama inference after the authorized, validated contract-v2 handoff. PostgreSQL keyword retrieval remains the product retrieval path. No online dense/hybrid retrieval, reranking or answer generation was added. No database migration or runtime dependency was required.

## Fixed comparison and winner

The checked-in [corpus](../backend/tests/fixtures/embeddings/benchmark.json) contains **68 labeled queries, 68 sources and 162 validated chunks**, including four repository-code snapshots, long continuation evidence, documentation, code, configuration, tables, exact identifiers, paraphrases, Japanese/Urdu/English technical text and thirty similar-but-wrong distractor sources. Gold chunk IDs were frozen before model comparison. Controlled labels are initial research evidence, not a guarantee for unseen private documents.

Winner rule established before comparison: maximize Recall@10, then Recall@5, then MRR@10; at each gate allow at most **0.01 absolute** quality difference, then prefer lower median warm latency and finally smaller download size. Gemma's Recall@10 lead excludes the other candidates before speed tie-breaking. Its MRR is also highest.

| Candidate | Recall@5 | Recall@10 | MRR@10 | Cold median s | Warm median ms | Chunks/s | Dimensions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PostgreSQL keyword | 75.74% | 86.03% | 0.699 | 0.022 | 2.65 | N/A | 0 |
| Qwen 0.6B | 94.12% | 95.59% | 0.846 | 1.964 | 61.08 | 14.56 | 1024 |
| **EmbeddingGemma 300M** | **94.85%** | **97.06%** | **0.906** | 3.127 | 66.80 | 22.68 | 768 |
| Nomic v1.5 | 89.71% | 93.38% | 0.849 | 1.170 | 59.10 | 23.35 | 768 |

Each candidate used three complete corpus passes, three complete query passes and three cold probes. Cold probes verify the model absent from `/api/ps`; operating-system file caches remain warm. Keyword cold measurements include a fresh connection/pool, not a restart of PostgreSQL or cache eviction. Warm dense timing includes query formatting/inference, transport validation and offline exact cosine ranking across the fixed corpus. Keyword timing includes the existing SQL query and mapping. These are different execution paths, both measured end-to-end for this small corpus.

Initial RAM sampling missed `llama-server`; it was corrected and Qwen was repeated. Initial unload scheduling was insufficient; Qwen/Gemma cold probes were corrected using verified unloaded state. Final committed measurements supersede those initial samples. GPU figures include desktop baseline; PostgreSQL RSS includes shared pages and other databases. No precision beyond those measurement limits is claimed.

| Candidate | Median/peak resident RAM MiB | Median/peak total GPU MiB | Model download MiB | Vector payload KiB for 162 chunks |
| --- | ---: | ---: | ---: | ---: |
| postgresql-keyword | 237.8/237.8 | 0/0 | 0.0 | 0.0 |
| qwen3-embedding:0.6b | 730.6/750.1 | 2322/2322 | 609.5 | 649.3 |
| embeddinggemma:300m | 1132.3/1642.2 | 716/3030 | 593.1 | 487.3 |
| nomic-embed-text:v1.5 | 503.3/530.0 | 392/392 | 261.6 | 487.3 |

Vector payload uses pgvector's `4 × dimension + 8` byte representation; heap/JSONB/index overhead is excluded. Full rankings, misses by type, sample timings, Ollama version, model quantization details and exact digests are committed in [embedding-results](embedding-results/). All compared requests succeeded without truncation errors. Gold recall measures all labeled relevant chunks, not merely source hits. Repeated query rankings were identical.

## Integration and persisted contract

`prepared_source(session, source_id, trusted_owner_id)` authorizes before artifact decoding and independently validates canonical/chunk projections. The worker embeds validated `retrieval_content`; profile-specific formatting stays outside canonical content. Qwen uses a query instruction and unchanged document text; original Gemma uses search query and title/text document prefixes; Nomic uses search_query/search_document prefixes. Tested Ollama templates pass prompts through unchanged, so prefixes are applied exactly once. Unbounded Gemma title labels fall back to `none`; complete heading evidence remains in canonical/chunk text.

Native `/api/embed` sends `truncate=false`, disables redirects/environment proxies, bounds response size/time, validates response model/count/dimensions/finite/nonzero vectors and verifies the selected model digest before/after inference. Local HTTP is restricted to explicit loopback origin; remote HTTPS requirements remain unchanged.

Vector commits refresh and revalidate source owner, content/version/artifact, actual chunk projections and access availability. Account-before-source row locks match connector lifecycle order. Final ACL lease checks use PostgreSQL wall-clock time rather than the potentially stale transaction-start timestamp. All vectors for a source commit atomically; provider/validation/persistence failures roll back and preserve keyword chunks. Safe failed status requires explicit retry. Remote HTTP provider truncation policy remains explicitly unknown in identity metadata; no local truncate guarantee is invented for that transport. Cancellation leaves a resumable processing state. No private text or credentials are logged.

Existing JSONB `embedding_identity` holds exact tag/digest, dimensions, formatting revision, truncation policy, L2 normalization, formatted input hash and configuration hash. Reuse requires exact identity. Older/unversioned vectors regenerate. Unchanged ingestion preserves vector identity; changed enriched text clears vectors. Owner/source/version/canonical hash, citations and continuation relationships remain separately resolvable through the existing prepared contract. Legacy JSONL/v1 sources must be reprocessed from the original; no v2 validation bypass exists.

## Persistence evidence

The actual local winner embedded a complete **14-chunk** contract-v2 continuation source in the isolated PostgreSQL benchmark database. All 14 vectors have dimension 768, correct source/chunk IDs and pinned identities; PostgreSQL reports 43,064 bytes for their stored vector values; prepared validation and keyword retrieval succeeded afterwards. This is real Ollama + PostgreSQL + pipeline persistence on a controlled source, not live provider/Clerk verification or a claim that every application source was embedded. [Evidence](embedding-results/persistence.json).

Live Ollama also rejected a deliberately oversized safe text input with truncate=false; the adapter returned a safe error. Mocked automated regressions cover malformed responses, missing/wrong vectors, NaN/Inf/boolean/zero vectors, timeout/unavailability, wrong identity/model, ownership mismatch, source/artifact/projection changes, lease expiry, reuse, rollback and keyword continuity.

## Reproduce

Run from repository root with installed Ollama and the existing Python environment. Supply a **different disposable PostgreSQL database ending `_test`**, never the application's database. No fixture reset/drop is performed by the benchmark; it applies existing migrations and registers only its deterministic private fixtures.

```bash
export EMBEDDING_BENCHMARK_DATABASE_URL='postgresql+asyncpg://.../fixflow_embeddings_benchmark_test'
myenev/bin/python -m scripts.benchmark_embeddings
myenev/bin/python -m scripts.benchmark_embeddings --model qwen3-embedding:0.6b
# Pull subsequent candidates only after the harness/Qwen run succeeds.
ollama pull embeddinggemma:300m
myenev/bin/python -m scripts.benchmark_embeddings --model embeddinggemma:300m
ollama pull nomic-embed-text:v1.5
myenev/bin/python -m scripts.benchmark_embeddings --model nomic-embed-text:v1.5
myenev/bin/python -m scripts.benchmark_embeddings --select
myenev/bin/python -m scripts.benchmark_embeddings --persist 4e0eb61b-5019-5e45-9089-9800bf5ead71
```

Candidate comparisons write only isolated `.local/embedding-benchmark` arrays/reports, never database vectors. Explicit `--persist` occurs after all candidate results match the same corpus hash and a winner is selected. Benchmark databases/results must not be shared with concurrently truncating pytest/browser fixtures. Timing requires an idle machine and sequential candidates.

## Operator configuration

Use the pinned [winner](../configs/embedding-winner.json) and [non-secret environment example](../configs/ollama.env.example). Set those values in native `backend/.env`, start `ollama serve` if needed, then restart FastAPI. Newly validated sources enter the existing persistent worker; one embedding worker/four inputs per batch is the starting policy. Do not mix model revisions/dimensions in a future query search path. Model changes require explicit regeneration/re-evaluation; existing indexed sources are not bulk overwritten automatically.

Loopback is local to each process network namespace: a bridge-network backend container cannot reach host Ollama using localhost. This release verifies native Linux execution; container Ollama deployment needs an intentionally shared local network namespace, not a relaxed remote HTTP rule. No public Ollama service was introduced.

Published references checked during implementation: [Ollama native embed](https://docs.ollama.com/api/embed), [model digests](https://docs.ollama.com/api/tags), [Qwen formatting](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [original Gemma formatting](https://huggingface.co/google/embeddinggemma-300m), [Nomic formatting](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5).

## Release gates

- Final full Python suite with disposable PostgreSQL and local embedding configuration active: **392 passed, 2 native-OCR skips**, one pre-existing LangChain deprecation warning. Final review added a remote-policy assertion and two connector-revocation cases; the affected suite passed **36 tests**. Enabling local configuration reproduced a fixture leak of model/dimensions; explicit empty test environment overrides fixed it before the final full run.
- Frontend: **118 passed** with coverage; ESLint, TypeScript and production build passed. No product UI or retrieval path was changed.
- Ruff, mypy, production Bandit and locked dependency audit passed. Alembic check: no new upgrade operations. Existing migration upgrade/downgrade and authorization/parsing/retrieval regressions ran in the Python suite.
- Native backend's non-secret local embedding fields were set to the pinned winner and validated against the installed model. Restart native FastAPI to load those values. Application credentials and application vectors were not overwritten by candidate comparison; only the explicit benchmark source persistence wrote vectors.

- Real Next/FastAPI/PostgreSQL/worker browser harness: **118 checks and 101 fixture outcomes passed**, including account isolation, beginning/middle/end keyword retrieval, ingestion, changed versions, persistence, malformed inputs and failure recovery. SQL: 103 sources, 219 documents, 3,806 chunks, zero orphans. Both embedding transports were deliberately disabled in this ingestion regression harness; real model/vector persistence is the separate 14-chunk evidence above. External Clerk identities were simulated, not live consent.
