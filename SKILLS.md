# Skills Required for FixFlow

Active implementations and repository-defined verification tools are listed; tools offered for local checks are distinguished from CI execution. No LLM diagnosis, reranker, prompt system, agent framework, or admin UI is implemented. Optional HTTP embeddings, trusted gateway account isolation and direct read-only Connected Apps are implemented; embeddings and connectors remain disabled until their respective server configuration is supplied.

| Skill | Used For | Important Locations |
| --- | --- | --- |
| Next.js 16 / React 19 / TypeScript | App Router screens, client state/effects, account pages, streaming route, contract mapping | `src/app/`, `src/proxy.ts`, `src/lib/{api,types,use-resource}.ts`, `next.config.ts` |
| Tailwind CSS 4 / accessible UI | Theme tokens, responsive layout, dialogs, keyboard input, loading/error states | `src/app/globals.css`, `src/components/`, `postcss.config.mjs` |
| Clerk | Frontend sessions, account entry, route/proxy authentication; verified server gateway identity plus backend record ownership | `src/proxy.ts`, `src/app/layout.tsx`, `src/app/sign-in/`, `src/app/sign-up/`, `src/lib/server/backend-proxy.ts`, `backend/services/access.py` |
| Python async / FastAPI / Pydantic | API validation, dependency injection, safe errors, settings, lifespan tasks | `backend/main.py`, `backend/api/routes.py`, `backend/config.py`, `backend/schemas/models.py` |
| PostgreSQL / async SQLAlchemy / asyncpg | UUID/FK integrity, JSONB snapshots/metadata, transactions, deduplication, advisory locking | `backend/db/`, `backend/services/{store,ingestion}.py`, `backend/repositories/` |
| PostgreSQL full-text search | Actual keyword retrieval using generated tsvector, GIN, OR query and rank | `backend/repositories/retrieval.py`, `backend/db/models.py` |
| pgvector | Nullable vector storage and tested future cosine-search interface; optional validated HTTP generation; retrieval remains keyword-based | `backend/services/embeddings.py`, `backend/repositories/vectors.py`, `backend/db/models.py`, `backend/tests/test_runtime_pipeline.py` |
| Alembic | Async schema migrations and readiness revision checking | `alembic.ini`, `backend/db/migrations/`, `backend/services/readiness.py` |
| Document extraction / LangChain loaders and splitters | PDF/HTML/DOCX/CSV/text extraction; heading-aware and recursive chunks, not LLM orchestration | `backend/processing/{loaders,chunking}.py`, `requirements-ingestion.txt` |
| YAML / standalone OKF concepts | Bounded safe frontmatter parsing, JSON-compatible metadata, explicit strict runtime mode and compatible ordinary Markdown fallback | `backend/processing/okf.py`, `backend/tests/test_okf.py` |
| Upload and API security | Private paths/permissions, containment, extension/size validation, untrusted text, safe CORS/errors | `backend/services/uploads.py`, `backend/services/ingestion.py`, `backend/main.py`, `src/lib/files.ts` |
| CLI / JSONL processing | Resumable batch import, explicit legacy ownership assignment, native PostgreSQL helper | `scripts/`, `backend/tests/test_database.py`, `backend/tests/test_runtime_pipeline.py` |
| Vitest / Testing Library / pytest / HTTPX | UI and API tests, mocked boundaries, disposable PostgreSQL integration, coverage | `tests/frontend/`, `backend/tests/`, `vitest.config.mts`, `pyproject.toml` |
| Playwright / Vite test harness | Browser verification with real components and optional real backend; Clerk/Next routing mocked | `tests/browser/` |
| Docker / Compose | Non-root app images, standalone frontend, DB health/migration ordering, persistent volumes | `Dockerfile.frontend`, `Dockerfile.backend`, `compose.yaml`, `.dockerignore` |
| ESLint / SonarJS / Ruff / mypy | Static quality and types | `eslint.config.mjs`, `pyproject.toml`, `package.json` |
| Bandit / pip-audit | Available local Python security/dependency checks; not invoked by the CI workflow | `requirements-dev.txt`, `AGENTS.md` |
| GitHub Actions / SonarQube | Coverage pipeline against ephemeral pgvector PostgreSQL; analysis upload | `.github/workflows/sonarqube.yml`, `sonar-project.properties`, `requirements-dev.lock.txt` |
| Native connector adapters | Gmail REST v1, Drive REST v3, GitHub App REST and Slack user OAuth/Web API, normalizing through one provider-independent envelope | `backend/connectors/`, `backend/schemas/connectors.py`, `docs/connectors.md` |
| Auth Broker / encrypted TokenVault | One-time owner-bound OAuth state, Google/GitHub PKCE, server-only exchange/refresh, encrypted credentials/previous-key rotation, safe local-first disconnect | `backend/services/connectors.py`, `backend/connectors/{base,vault}.py` |
| Durable connector synchronization | Resource selection, page/resource checkpoints, incremental cursors, retries/rate limits, scheduled reconciliation, resource/version deduplication | `backend/services/{connector_sync,connector_sources}.py`, `backend/db/models.py`, `backend/db/migrations/versions/0004_connector_layer.py` |
| Provider events / permission evidence | Signed/identity-checked events, replay receipts, optional watches, owner-private ACL context and SQL retrieval prefilters | `backend/connectors/{events,subscriptions}.py`, `backend/processing/pipeline/context.py`, `backend/repositories/source_access.py` |
| Connected Apps UI | Real OAuth redirects/callback status, discovery pagination, provider-specific options, polling/progress, reauth/errors, explicit retain/soft-delete/purge | `src/app/connectors/`, `src/components/connectors/`, `src/app/api/connectors/`, `src/lib/{connectors,connector-contracts}.ts` |
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
