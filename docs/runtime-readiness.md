# Runtime ingestion and security audit

Reviewed and implemented on 2026-10-02. This document describes the current request, ingestion, persistence, and authentication paths.

The working path is: Clerk account → same-origin Next gateway → authenticated private FastAPI → account-owned source → private upload → extraction/strict standalone OKF parsing → transactional documents/chunks → keyword retrieval. Optional configured embeddings continue through validated HTTP batches → atomic pgvector persistence. Diagnosis generation remains disabled.

| Gap and consequence | Implemented change | Verification |
| --- | --- | --- |
| Frontend sign-in did not protect FastAPI; all accounts could read shared records and retrieval evidence. | Clerk-verified gateway replaces caller identity/credentials; backend verifies its server credential before reading bodies. Ownership filters cover sources, sessions, chat, saved records, keyword retrieval, dormant vector searches/deletion/counts, and private counts. Missing configuration fails closed. | Negative gateway tests and two-account PostgreSQL/API tests, including cross-account chat and deduplication. |
| Global file/chunk deduplication could return or collide with another account's content. | File uniqueness is `(owner_id, file_hash)`; live chunk identifiers include source UUID. Different ingestion formats for identical content return 409. | Identical documents uploaded by two users persist independently and remain isolated. |
| Extension-only upload checks accepted binary/empty text and disguised PDF/DOCX; oversized request bodies could be parsed before validation. | Declared and streamed request limits, 50 MiB file cap, strict UTF-8/nonblank/null-byte checks, PDF signature, DOCX member/encryption/expansion checks. Extraction has 5 million character and 10,000 chunk default limits. | Invalid-content cleanup, request-size tests and transactional extraction-limit rollback. |
| Optional Markdown parsing silently treated invalid OKF as ordinary text. | Explicit `ingestion_format=okf` in API/UI validates standalone Markdown frontmatter and persists a safe failed job with no corpus rows on invalid input. Ordinary document mode retains compatible parsing. | Valid metadata/body roundtrip and invalid strict-format rollback/retrieval tests. |
| Vector storage existed without a runtime generator, failure state, or retry workflow. | Optional HTTPS adapter and worker, configured model/dimension, timeout, bounded responses, exact batch indexes and finite nonzero vector validation. Source-level advisory lock and transactional vectors; restart recovery and explicit failed-job retry. | Invalid provider responses, later-batch rollback, failure/retry/completion using test doubles and PostgreSQL. No paid or real embedding endpoint was called. |
| Frontend TypeScript assertions accepted malformed backend JSON. | Runtime checks for consumed responses, restored input fields, collections, enums and counts; safe error/loading/retry behavior. API schemas reject null bytes and invalid Unicode before JSONB persistence, including tags and source titles. | Frontend malformed-response regression tests and production build. |

## Configuration and migration

Set one random `FIXFLOW_API_TOKEN` of at least 32 ASCII characters in native `.env.local` and `backend/.env`, or root `.env` for Compose. Never prefix it with `NEXT_PUBLIC_`. The browser sends no trusted user ID or service credential. Next uses `INTERNAL_API_URL` with legacy `NEXT_PUBLIC_API_URL` fallback; Compose supplies its private backend hostname.

Alembic `0002` adds ownership and job metadata, preserving rows and vector dimensions. Existing records are assigned `__legacy__`, not automatically exposed to Clerk accounts. The legacy JSONL importer uses that same isolated owner. Assign only after confirming the intended account:

```bash
myenev/bin/python -m scripts.assign_legacy_owner --owner user_VERIFIED_ID
# Review the count-only preview, then explicitly commit:
myenev/bin/python -m scripts.assign_legacy_owner --owner user_VERIFIED_ID --apply
```

Assignment preserves IDs, documents, chunks, messages, and snapshots; account file-hash conflicts abort rather than delete or merge records. Stop writers during an administrative assignment. Migration downgrade similarly refuses cross-account duplicate hashes rather than discard data.

This workspace's ignored native environment was given a matching gateway token. Its application database was backed up privately and upgraded to `0002`; row counts across all six application tables were unchanged, and `alembic check` found no pending model changes. No legacy ownership assignment was inferred or performed. Restart existing Next/FastAPI processes to load configuration and code changes. Other checkouts must configure the token and run `alembic upgrade head`.

## Optional embeddings

Embeddings stay disabled until configured, per the user's decision. Set server-side:

```text
EMBEDDING_API_URL=https://your-provider.example/embeddings
EMBEDDING_MODEL=your-selected-model
EMBEDDING_DIM=your-selected-dimension
EMBEDDING_API_KEY=your-private-key-if-required
```

The endpoint contract is POST JSON `{"model":"configured-model","input":["chunk text"]}` with optional bearer key, returning `{"data":[{"index":0,"embedding":[0.1,0.2]}]}`. Every batch requires each zero-based index exactly once and every vector must match the configured dimension. HTTPS is required; redirects are rejected. Default timeout: 30 seconds; batch: 16; provider response cap: 16 MiB. Pool capacity must exceed twice `INGESTION_WORKERS` when enabled so concurrent jobs can commit status updates.

States: `ready_for_embedding/not_configured` when disabled; configured jobs move through `embedding/processing` to `indexed/complete` only after vectors commit. Provider failure rolls back that attempt's vectors, retains keyword-ready chunks, and records a safe error. The owner may POST `/api/sources/{id}/retry-embedding` through the gateway. Failed jobs do not automatically incur repeated provider requests; interrupted processing jobs resume after restart. Legacy sources are excluded from automatic external embedding until explicitly assigned.

Indexed sources are not automatically re-embedded after a model change. Plan an explicit re-embedding migration/job before changing models. Product retrieval remains keyword-based even after embeddings exist; vector/hybrid retrieval, reranking, and diagnosis generation require subsequent implementation and evaluation.

## Executed checks

- PostgreSQL-backed Python suite: 89 passed, zero skipped; includes upgrade preservation and legacy assignment preview/conflict tests. One existing LangChain loader deprecation warning.
- Frontend suite: 78 passed. ESLint and TypeScript passed.
- Next.js production build passed, including the dynamic gateway route.
- Ruff and mypy passed; application database `alembic check` passed.
- pip-audit found no known vulnerabilities in the resolved development requirements.
- Bandit production source scan passed. The unfiltered scan reports low-severity test assertions and controlled test migration subprocesses in the executed full scan, with no medium/high findings; these were not suppressed in source.

Unit/integration doubles verify gateway trust and provider failures. They do not establish live Clerk sign-in, external embedding availability, Docker image execution, internet deployment capacity, or a guarantee against all malicious parser inputs. The browser harness uses a test-only synthetic trusted principal; it never weakens production authentication.

## Current limits

The app has no account upload/storage/API quotas, rate limits, retention policy, production backup/restore monitoring, or isolated parser workers for hostile workloads. FastAPI is bound privately in the supplied Compose setup. The server gateway credential delegates verified identity and must remain secret. Direct public-client token verification, admin roles, ZIP OKF bundle graph import, OCR, repository/remote URL fetching, LLM diagnosis, and vector retrieval are not implemented.
