# Skills Required for FixFlow

Active implementations and repository-defined verification tools are listed; tools offered for local checks are distinguished from CI execution. No LLM diagnosis, reranker, prompt system, agent framework, or admin UI is implemented. Optional HTTPS/local Ollama embeddings, trusted gateway account isolation and direct read-only Connected Apps are implemented; embeddings and connectors remain disabled until their respective server configuration is supplied.

| Skill | Used For | Important Locations |
| --- | --- | --- |
| Next.js 16 / React 19 / TypeScript | App Router screens, client state/effects, account pages, streaming route, contract mapping | `src/app/`, `src/proxy.ts`, `src/lib/{api,types,use-resource}.ts`, `next.config.ts` |
| Tailwind CSS 4 / accessible UI | Theme tokens, responsive layout, dialogs, keyboard input, loading/error states | `src/app/globals.css`, `src/components/`, `postcss.config.mjs` |
| Clerk | Frontend sessions, account entry, route/proxy authentication; verified server gateway identity plus backend record ownership | `src/proxy.ts`, `src/app/layout.tsx`, `src/app/sign-in/`, `src/app/sign-up/`, `src/lib/server/backend-proxy.ts`, `backend/services/access.py` |
| Python async / FastAPI / Pydantic | API validation, dependency injection, safe errors, settings, lifespan tasks | `backend/main.py`, `backend/api/routes.py`, `backend/config.py`, `backend/schemas/models.py` |
| PostgreSQL / async SQLAlchemy / asyncpg | UUID/FK integrity, JSONB snapshots/metadata, transactions, deduplication, advisory locking | `backend/db/`, `backend/services/{store,ingestion}.py`, `backend/repositories/` |
| PostgreSQL full-text search | Actual keyword retrieval using generated tsvector, GIN, OR query and rank | `backend/repositories/retrieval.py`, `backend/db/models.py` |
| pgvector | Nullable vector storage and tested future cosine-search interface; validated HTTPS/Ollama generation through prepared_source, exact model/input reuse identity; product retrieval remains keyword-based | `backend/services/embeddings.py`, `backend/repositories/vectors.py`, `backend/db/models.py`, `backend/tests/test_runtime_pipeline.py` |
| Alembic | Async schema migrations and readiness revision checking | `alembic.ini`, `backend/db/migrations/`, `backend/services/readiness.py` |
| Legacy document extraction / LangChain loaders and splitters | Reachable manual JSONL import and legacy/test helper; 3,000-character chunks / 400 overlap, excluded from contract-v2 prepared handoff | `backend/processing/{loaders,chunking}.py`, `requirements-ingestion.txt` |
| Canonical / OKF / atomic knowledge processing | Structural nested concepts, complete ordered units, evidence-based aliases, private owner/source context; no model-inferred relationships | `backend/schemas/pipeline.py`, `backend/processing/pipeline/{parsers,canonical,concepts,units,context}.py` |
| Concept-first structural chunking | Whole concepts then child/direct units; sentence/list/AST/subtree/row boundaries; content-specific lexical continuations for oversized valid atoms, with 1,024 UTF-8-byte default and zero content overlap | `backend/processing/pipeline/{chunks,splitting,runner}.py` |
| Difficult-content experiments and reconstruction | Measured candidate comparisons; ordered continuation groups, exact offsets/full-unit hashes, JSON/YAML container identity, XML paths, table schema/row/cell references, lexical code declarations; explicit uncertain-layout exclusion | `backend/tests/{difficult_corpus,test_difficult_content}.py`, `scripts/benchmark_difficult_content.py`, `docs/difficult-content.md` |
| Deterministic chunk handoff / loss validation | Exact selectors, hashes/versions, neighbors, citation provenance, independent coverage, pre-write validation and authorized SQL reconstruction | `backend/processing/pipeline/validation.py`, `backend/repositories/{pipeline,prepared}.py`, `backend/tests/test_prepared_pipeline.py` |
| YAML / standalone OKF concepts | Bounded safe frontmatter parsing, JSON-compatible metadata, explicit strict runtime mode and compatible ordinary Markdown fallback | `backend/processing/okf.py`, `backend/tests/test_okf.py` |
| Upload and API security | Private paths/permissions, containment, extension/size validation, untrusted text, safe CORS/errors | `backend/services/uploads.py`, `backend/services/ingestion.py`, `backend/main.py`, `src/lib/files.ts` |
| CLI / JSONL processing | Resumable batch import, explicit legacy ownership assignment, native PostgreSQL helper | `scripts/`, `backend/tests/test_database.py`, `backend/tests/test_runtime_pipeline.py` |
| Vitest / Testing Library / pytest / HTTPX | UI and API tests, mocked boundaries, disposable PostgreSQL integration, coverage | `tests/frontend/`, `backend/tests/`, `vitest.config.mts`, `pyproject.toml` |
| Playwright / real Next E2E / Vite component harness | `run-preembedding.mjs` uses actual Next/gateway/FastAPI/PostgreSQL/worker/prepared with external Clerk only simulated in an isolated copy; older Vite harnesses mock Next routing | `tests/browser/`, `docs/preembedding-validation.md` |
| Docker / Compose | Non-root app images, standalone frontend, DB health/migration ordering, persistent volumes | `Dockerfile.frontend`, `Dockerfile.backend`, `compose.yaml`, `.dockerignore` |
| ESLint / SonarJS / Ruff / mypy | Static quality and types | `eslint.config.mjs`, `pyproject.toml`, `package.json` |
| Bandit / pip-audit | Available local Python security/dependency checks; not invoked by the CI workflow | `requirements-dev.txt`, `AGENTS.md` |
| GitHub Actions / SonarQube | Coverage pipeline against ephemeral pgvector PostgreSQL; analysis upload | `.github/workflows/sonarqube.yml`, `sonar-project.properties`, `requirements-dev.lock.txt` |
| Native connector adapters | Gmail REST v1, Drive REST v3, GitHub App REST and Slack user OAuth/Web API, normalizing through one provider-independent envelope | `backend/connectors/`, `backend/schemas/connectors.py`, `docs/connectors.md` |
| Auth Broker / encrypted TokenVault | One-time owner-bound OAuth state, Google/GitHub PKCE, server-only exchange/refresh, encrypted credentials/previous-key rotation, safe local-first disconnect | `backend/services/connectors.py`, `backend/connectors/{base,vault}.py` |
| Durable connector synchronization | Resource selection, page/resource checkpoints, incremental cursors, retries/rate limits, scheduled reconciliation, resource/version deduplication | `backend/services/{connector_sync,connector_sources}.py`, `backend/db/models.py`, `backend/db/migrations/versions/0004_connector_layer.py` |
| Provider events / permission evidence | Signed/identity-checked events, replay receipts, optional watches, owner-private ACL context and SQL retrieval prefilters | `backend/connectors/{events,subscriptions}.py`, `backend/processing/pipeline/context.py`, `backend/repositories/source_access.py` |
| Connected Apps UI | Real OAuth redirects/callback status, fixed public-origin gateway checks and redirects behind Docker, discovery pagination, provider-specific options, polling/progress, reauth/errors, explicit retain/soft-delete/purge | `src/app/connectors/`, `src/components/connectors/`, `src/app/api/connectors/`, `src/lib/{connectors,connector-contracts}.ts`, `src/lib/server/application-origin.ts` |
| Logging / readiness | Server failure logs use exception types plus request/source identifiers; read-only DB/vector/schema/revision/count probes | `backend/main.py`, `backend/services/{ingestion,readiness}.py` |

Development and verification commands, architectural constraints, and data-safety rules are in `AGENTS.md`; the current system audit is in `docs/runtime-readiness.md`.

## Implemented user-facing connector capabilities

All four require operator-supplied server credentials and provider approval/installation. Automated tests exercise mocked external boundaries; they do not establish live provider consent or delivery. There is one direct native transport per provider, with no managed Google Cloud connection.

| Capability | Status | Actual behavior and bounds |
| --- | --- | --- |
| Connect Gmail | IMPLEMENTED | Authorize server-side Google read-only access, list/select labels and UTC dates, sync individual messages with thread/header metadata, optionally retrieve separate attachments; history updates and controlled selected-label address/date queries; disconnect/revoke. |
| Connect Google Drive | IMPLEMENTED | Discover/select files, folders and shared drives; synchronize authorized supported originals or DOCX/XLSX/PPTX Workspace exports; preserve native permissions/metadata and separate user/shared-drive change cursors; disconnect/revoke. Shortcuts are not followed; export/download/parser limits apply. |
| Connect/install GitHub App | IMPLEMENTED | Discover user-authorized installed repositories; select branches/categories; synchronize individual code/README/docs, issues/comments, PRs/reviews/review comments, commits/releases; resume truncated trees by subtree; preserve empty repository metadata; paginate commit/PR files within the native 3,000-file bound; process selected-category events and installation removals. Disconnect revokes the user token; installation administration remains provider-side. |
| Connect Slack workspace | IMPLEMENTED | Discover/select authorized public/private channels, UTC dates, messages/threads/replies, authors and file metadata; supported file downloads and verified events; disconnect/revoke. Thread/event date bounds and user-specific token revocation are enforced. No DM scopes; history/replies use durable native rate reservations. |
| Select resources / Sync now / Reconcile | IMPLEMENTED | Provider authorization validates choices; durable initial/incremental/reconciliation jobs resume checkpoints/rate delays. UI shows real connection/provider/auth/sync states and fetched/queued counts; Knowledge Sources shows actual parsing/chunk/embedding status and retrieval availability. |
| Private retrieval / ACL propagation | IMPLEMENTED | Native evidence travels through envelope, canonical, OKF and chunks. SQL filters exclude another owner's, disconnected, removed or expired-access content before retrieval. Lost selected roots reconcile the authorized remainder; permission-removal events cancel stale jobs. Visibility remains owner-private, with eventual provider ACL checks and expiring leases. |
| Verified event callbacks | IMPLEMENTED; optional setup | Gmail authenticated Pub/Sub, token-verified Drive channels, GitHub HMAC, Slack HMAC/timestamp and replay receipts; authoritative asynchronous fetching with periodic polling/reconciliation fallback. Operator delivery configuration is required. |
| Retain / Soft-delete / Purge | IMPLEMENTED | All policies stop sync/wipe credentials/disable retrieval locally before best-effort remote cleanup. Retain keeps disabled content; soft-delete marks removed; purge removes connector source artifacts/chunks/private files. Independent saved/history snapshots remain. |
| Controlled provider queries | IMPLEMENTED bounded interface | Selected-resource list/count APIs and typed frontend client, bounded dates/address filters where supported, limits/cursors and page counts. Counts refer to each page; no query planner or mailbox-wide totals UI is provided. |

Connector regression verification includes all four actual native adapters against mocked external HTTP and disposable PostgreSQL, with real parsing, canonical/OKF generation, chunk persistence, private retrieval, token rotation and disconnect. UI controls include discovery pagination, clearing selections, health checks, Sync Now/reconciliation, background polling, reauthentication and disconnect policies. This does not verify live provider consent or webhook delivery.

Setup, exact callbacks/scopes, tests, commands and remaining operational work are documented in `docs/connectors.md`. Never advertise embeddings, AI diagnosis, live authorization or a successful remote sync from mocked tests or a queued HTTP 202.

## Working with the verified ingestion/chunking subsystem

This is the existing repository skill documentation, not a new duplicate `SKILL.md`. Final matrix, fixes, timings, provenance examples and limitations: [preembedding-validation.md](docs/preembedding-validation.md). Architecture: [ingestion-pipeline.md](docs/ingestion-pipeline.md). Historical difficult-content comparisons: [difficult-content.md](docs/difficult-content.md).

### Purpose and boundary

Produce deterministic, authorized, citation-ready evidence for later embedding research. Artifact contract **2**, engine **2.1.2**, native parser **5**. No embedding model was selected/configured for this verified stage; no vectors, vector product retrieval, reranking or LLM generation were added. Existing optional HTTP embedding/vector modules remain separate and unconfigured in this verification. PostgreSQL keyword retrieval remains current behavior.

### Production processing path

Browser upload/paste or connector `RawSourceEnvelope` → trusted owner/source registration/private raw bytes/SHA-256 → persistent ingestion job → bounded spawned processing → inspection/parser selection → canonical document → deterministic structural/OKF concepts → ordered atomic units → concept-first content-routed chunks → independent validation/coverage/reconstruction → PostgreSQL transaction → ready status → authorized `prepared_source` revalidation.

Locations: `services/{uploads,ingestion,connector_sources}.py`, `processing/pipeline/{inspection,parsers,canonical,concepts,units,chunks,splitting,validation,runner,execution}.py`, `schemas/pipeline.py`, `repositories/{pipeline,prepared,source_access,retrieval}.py`. Provider transport/credentials end at the source envelope.

### Supported inputs and routing

`inspection.py:EXTENSIONS` / `CODE_LANGUAGES` and `src/lib/files.ts` define allowlists. Payload signatures, MIME/extension hints, UTF-8 and available content inspection determine modality; file-picker help text is not a capability specification. Native adapters cover text/RST, Markdown, HTML/HTM, JSON, YAML/YML, XML, CSV, DOCX, PPTX, XLSX, digital PDF, source code, logs, EML and VTT/SRT.

Python uses AST where possible. JavaScript, TypeScript, JSX/TSX, Java, C/C++, Go, Rust, C#, shell, SQL, Ruby and PHP have tested conservative lexical/fallback handling; do not claim full grammars. PDF escalates to geometry/table extraction; detected rotated/gutter-spanning uncertainty fails quality gates. Scans/images require configured OCR; audio/video require a trusted installed transcription adapter. Accepted extension alone is not successful decoding. Missing/corrupt/unconfigured inputs fail explicitly and accepted raw sources remain intact.

### Canonical, concept and atomic contracts

`CanonicalDocument` holds source/hash/version/scope, profile, ordered typed blocks, section hierarchy, assets, grounded relationships, provenance, quality and deterministic hash. Each block belongs to exactly one section and retains structure/content hash/location. Do not fabricate assets, confidence or unavailable measurements.

`Concept` is a structural subject/section/function/subtree with label/path, source/direct block memberships, parent/children, content/dependency hashes and source context. No model infers meaning/semantic relationships. Direct memberships partition canonical evidence; parent concepts can retain descendants for later expansion. SQL `Document` UUIDs project concepts and differ from the source-wide canonical document ID.

`AtomicUnit` is complete ordered canonical evidence with source/canonical/concept/block IDs, type, structure, content, provenance, hash and authorization scope. Keep complete atoms even when retrieval fragments are small.

### Chunk and continuation contracts

Preserve whole fitting concepts, then child/direct structural units, paragraphs/sentences/statements/subtrees/table row groups. Oversized atoms use smaller syntax/lexical boundaries and exact continuations. Default maximum: **1024 UTF-8 bytes including retrieval context**; preferred minimum **16**, with complete-unit and continuation-tail exceptions. `token_count` measures this configured budget, not embedding-model tokens. Zero sliding overlap; necessary table schema/heading context can repeat.

Prose: sentence/clause/lexical boundaries. Code: Python AST and other conservative lexical/symbol evidence. JSON/YAML: complete paths/subtrees/array ranges and ancestor object/array types, preserving key/value relationships. XML: SAX structural anchors with complete original syntax reconstructable. Tables: schema/row groups then row/cell/header continuations with row/column identity. YAML comments remain evidence; huge/inexact numbers can retain validated lexical serialization rather than lossy conversion. Never label a continuation standalone JSON/XML/function syntax.

`UnitSlice` selects exactly one canonical character span (end-exclusive), table row range (one-based/inclusive), or JSON-pointer/key/array range (array zero-based/end-exclusive). `Continuation` declares group ID, **zero-based** index, count, full-unit hash, strategy and structural/row/column context; display Part labels are one-based. Joining ordered continuation selections must reproduce the complete normalized atom exactly. Independently validate count/index/offset/hash/path/scope; never deduplicate equal parts at different positions.

Chunks retain global order, reciprocal neighbors across concepts, parent/container links, section/source block references, raw/retrieval content, hashes, lengths, source context, authorization, provenance and exact unit selectors. `parents` and concepts resolve hierarchy. Canonical character offsets are not automatically raw-file byte offsets; `line_scope` distinguishes precise fragment lines from normalized-unit source ranges.

### Provenance, authorization, hashes and versions

Maintain chunk → slice/atom → concept → canonical source. Preserve source/canonical identities, source hash/version, section/path and available page/bbox/line/sheet/cell/slide/timestamp locations. Do not invent precision. Scope/context resolve the trusted private source row: ownership comes from server authentication/registered source, never a client user ID.

Keep connector availability/disconnection/removal/credential loss and expiring ACL leases in SQL prefilters. Provider secrets never enter evidence. Native EML retains ordered/duplicate `email_headers` as classified non-retrieval metadata; preferred body/subject remains searchable. Raw MIME retains alternatives/attachments not separately projected by native EML.

Manual same-owner identical bytes deduplicate by `(owner_id,file_hash)` and retain original source identity/name. Different owners and distinct connector external resources retain identities for identical bytes. Same-source changed content increases version; transactional replacement preserves source identity. Failed updates retain prior valid artifacts/projections; raw originals/failure reasons remain for reprocessing. Old v1/JSONL needs original-source v2 reprocessing; never weaken validation. Old validated v2 EML needs explicit force/update processing for new header metadata. Engine/config changes invalidate cache reuse when reprocessing occurs.

### Validation, coverage and prepared_source

`validate_result` independently verifies nonempty/schema/UTF-8 evidence, all references, scope/context, hashes/order, neighbors/parents, budget, table schema/paths/selectors, continuations and complete canonical coverage before writes. Do not validate loss merely by rerunning the splitter. Persist only validated data.

Original→canonical extraction and canonical→chunk coverage are separate measurements. Exact spans/leaf/row/context-heading coverage requires 100% retrieval-relevant canonical content, zero uncovered elements and zero accidental duplicate positions. Normalize whitespace only where justified. Exclusions require deterministic classification; retain raw originals. PDF extracted-word recall and known-layout ordering assertions do not certify every unseen visual layout.

Use `await prepared_source(session, source_id, trusted_owner_id)` in `repositories/prepared.py`. SQL filters owner/status/availability/connector lease **before** artifacts are read. Foreign/unavailable/not-ready sources return `None`; v1/corrupt/incomplete contracts raise `ValueError`. The reader validates the artifact and compares actual source/document/chunk projections, IDs, content, hashes, context and versions. No ready-label/raw-text shortcut is acceptable.

Returned `PipelineResult`: canonical source/version/hash, concepts, atoms, chunks, parents, engine/config/stage hashes, coverage/statistics. Chunks: deterministic IDs, raw/retrieval text, type/path/order/neighbors, scope/context, provenance, exact slices/continuations. SQL chunk UUID is `uuid5(UUID(source_id), chunk.chunk_id)`; SQL concept-document UUID is `uuid5(UUID(source_id), chunk.concept_id)`. Source/enriched-text hashes and versions also resolve through result/metadata. Future embeddings must consume this authorized contract and preserve citations, owner/ACL, version/hash, order and reconstruction metadata.

### Status/UI behavior

`uploaded → processing → chunked → ready_for_embedding`; `chunked` is inside the final transaction, not a guaranteed observable intermediate state. Failed/unvalidated evidence cannot pass prepared handoff. Never label unembedded data indexed. Optional later embedding states already exist, but no embedding ran in this verification. HTTP 202 means acceptance.

Pending UI polls run every two seconds, stop after three consecutive failures with a Refresh instruction, and stop at terminal states. Manual Refresh resumes. Preserve last known source state and the form on request failure. File-picker and paste/keyboard submit are supported; drag/drop is not. Keep long filenames/status text usable at 320/390/1440px.

### Extending parsers and splitters safely

For parsers, inspect routing/config/signatures first; implement the existing Parser protocol/Builder or trusted installed adapter factory; converge on the same canonical model. Preserve ordered evidence/raw originals/locations, report unknown quality as null, reject unsafe extraction explicitly. Allowlist changes require synchronized backend/frontend and supported/negative fixtures. Bump parser/engine/adapter revision for cached behavior changes.

For splitters, extend content routing/atomic selectors, not the ingestion/storage architecture. Prefer measured structural boundaries, then exact continuations. Preserve deterministic IDs/order/scope/provenance and every atom mapping; keep context-inclusive budgets. Inspect produced chunks and add independent loss/tampered-span, Unicode, repeated/versioned evidence, table/path/symbol, reconstruction and persisted handoff tests. No arbitrary overlap or extra storage model without evidence.

Required regression files: `test_{pipeline,pipeline_database,pipeline_integrity_audit,prepared_pipeline,difficult_content,preembedding_handoff}.py`. Maintain connector/security/owner, keyword sentinel, migration/rollback, frontend status/gateway and actual-browser checks when their consumers change. Do not skip difficult inputs, weaken assertions, fake OCR/provider output, or lose raw failed sources.

### Commands, safety and evidence

Use existing tooling and explicitly disposable PostgreSQL. Tests truncate/migrate their databases; never point them at application data. Use separate `_test` databases for concurrent pytest/browser runs. Complete production Next builds before the real-browser run.

```bash
TEST_DATABASE_URL='<disposable PostgreSQL URL ending _test>' myenev/bin/python -m pytest
myenev/bin/python -m ruff check backend scripts tests/browser/preembedding.py
myenev/bin/python -m mypy backend scripts tests/browser/preembedding.py
myenev/bin/python -m bandit -r backend scripts -x backend/tests,scripts/tests -q
DATABASE_URL='<disposable PostgreSQL URL ending _test>' myenev/bin/alembic check
npm run lint
npm run typecheck
npm run test:coverage
npm run build
TEST_DATABASE_URL='<separate disposable browser PostgreSQL URL ending _test>' \
  FIXFLOW_BROWSER_TOOLS=/path/to/installed/playwright \
  node tests/browser/run-preembedding.mjs
```

The browser command launches/stops actual Next/FastAPI on 3012/8012, applies migrations, resets only the disposable database, generates fixtures, validates 101 source outcomes plus recovery/security/version/retrieval/integrity. A fresh copied application in `.local/preembedding/app` contains code/config without env files and test-only external Clerk aliases; Next proxy/routing/gateway remain real. Never propagate those aliases/cookies into production. Logs/JSON/JUnit/traces/screenshots/SQL examples are in `.local/preembedding/`, coverage in `coverage/`. ESLint excludes generated `.local` builds/uploads while still checking tracked application/harness code; `tests/frontend/tooling.test.ts` proves both boundaries.

### Verified limitations and embedding boundary

Native Tesseract is absent in this verified host: two live OCR tests skip, deterministic downstream adapters pass, scanned uploads fail explicitly. No transcription adapter is configured. Detected rotated/gutter-spanning PDFs fail with raw retained; arbitrary complex visuals/Office layouts and full non-Python grammars are not certified. Native EML projects preferred body, not every MIME part. Operational upload/archive/page/block/character/chunk/deadline/memory/result limits remain. Legacy/corrupt/incomplete/foreign evidence stays excluded.

The next stage can consume validated authorized `prepared_source` results directly, deliberately choosing `raw_content` or `retrieval_content` and retaining provenance, ACL scope, source version/hash, continuation selectors and processing/model-input identity. Never reconstruct citations from a vector or bypass this reader. Embedding/model selection/vector retrieval/reranking/LLM answers belong to a later authorized task; preserve current keyword behavior. Live Clerk/OAuth/provider delivery/OCR claims require their own real execution evidence.

### Local embedding engineering

Native Ollama adapter: `backend/services/ollama.py`; model formatting: `embedding_profiles.py`; worker: `embeddings.py`. Use validated enriched retrieval content; preserve authorization/citations/version and continuation metadata separately. Before commit refresh the prepared handoff and check wall-clock lease validity. Native transport uses loopback HTTP and truncate=false; remote providers continue to require HTTPS. Exact digest/format/dimension/input identity controls reuse.

Fixed gold corpus, offline dense/keyword comparisons, resource measurements and quality-first selection: `scripts/benchmark_embeddings.py`, `backend/tests/fixtures/embeddings/benchmark.json`, `docs/local-embeddings.md`, `docs/embedding-results/`. Test-only database is mandatory and application vectors remain untouched during comparison. The tested winner is original `embeddinggemma:300m` (768, gemma-v1), pinned to the recorded digest. Product semantic retrieval is implemented in the subsequent retrieval stage; hybrid remains experimental, and reranking/generation remains unimplemented.


### Semantic retrieval evaluation and release

Use `retrieval_config.py` for the winner pin, `retrieval.py` for query formatting/inference/validation/fallback, and `VectorRepository.search_pinned` for authorized exact cosine retrieval. Consume prepared sources, verify full model/input identities and preserve source/version/citation/continuation data. Never use the legacy unpinned search in product requests. Keep `repositories/retrieval.py:search_chunks` unchanged as baseline/fallback, and recheck wall-clock access after inference before any keyword response.

Automatic embedding defaults off and consumes only pending/processing jobs when enabled. Before starting/restarting FastAPI, inspect this guard; do not sweep old ready sources. Partial embedding coverage must use keyword fallback so searchable unembedded sources do not disappear.

Reuse the fixed benchmark via `scripts/benchmark_retrieval.py`, then produce failure analysis with `scripts/retrieval_report.py`. Use an isolated *_test PostgreSQL DB and actual pgvector queries. Run timings without concurrent builds/tests, at least three repetitions with stable rankings. Compare keyword/dense before RRF; do not select fusion because it is customary. Current decision is dense plus keyword fallback; experimental RRF lowers Recall@5/MRR for a small Recall@10 gain. See `docs/semantic-retrieval.md`. Regressions cover query failures, pins, owner/lease/revocation, damaged projections, empty/mixed vectors, API wiring and metadata. No new dependency, schema, chunking, generation or reranker is required.
