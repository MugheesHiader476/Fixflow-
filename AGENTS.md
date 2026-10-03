# FixFlow — Coding Agent Instructions

## Next.js version warning

This repository uses Next.js 16.x (`package.json`: 16.3.5), which differs from older APIs and routing conventions. Before Next.js-specific changes, inspect the installed version, read relevant `node_modules/next/dist/docs/` documentation when available, and follow current repository patterns. Preserve any Next-generated agent warning if `next dev` regenerates this file.

## Product and architecture

FixFlow is a debugging workspace with account-owned data: upload documentation, search it with error/code/context, preserve sessions and follow-up conversations, and save findings. **Current behavior is keyword documentation retrieval, not AI diagnosis.**

- `src/app/layout.tsx`: Clerk, theme, toast providers; `src/app/page.tsx`: workspace and `/?session=...` restoration.
- `src/app/{sources,history,saved,settings,connectors}/page.tsx`: uploads/status, sessions, saved findings, readiness and Connected Apps.
- `src/components/{debug,layout,ui}/`: workflow components, shell, shared controls.
- `src/lib/api.ts`: browser → same-origin `/api/backend` → Clerk-authenticated Next gateway → private FastAPI; `src/lib/types.ts` defines the frontend subset of `backend/schemas/models.py`, validated on receipt by `src/lib/response-validation.ts`; not generated types. Keep consumed contracts synchronized, including `repoUrl` → `repo_url`.
- `src/proxy.ts`: Clerk frontend protection; `src/app/sign-in/[[...sign-in]]/page.tsx` and `src/app/sign-up/[[...sign-up]]/page.tsx`: account entry.
- `src/app/api/backend/[...path]/route.ts` and `src/lib/server/backend-proxy.ts`: authenticated streaming gateway; the UI uses `/api/backend/api/documents` for uploads.
- `backend/main.py`: FastAPI app, safe errors, request authentication/body guard, validated CORS, public `/health`, ingestion/embedding worker lifespan; `backend/api/routes.py`: `/api` routes and request validation.
- `backend/services/store.py`: retrieval → provider → persisted diagnosis/chat/saved snapshots; `diagnosis.py`: `DiagnosisProvider` contract and default `DocumentationProvider`.
- `backend/services/{uploads,ingestion,embeddings,access,readiness}.py`: private uploads, persistent job processing, read-only DB readiness.
- `backend/processing/pipeline/`: content inspection, native/layout/optional OCR adapters, quality/canonical/OKF/chunk gates, cache and bounded process execution. `backend/schemas/pipeline.py` is the parser-independent contract; `backend/repositories/pipeline.py` persists artifacts and incremental projections. `backend/processing/{loaders,chunking}.py` remains for the legacy importer; `okf.py` supplies bounded standalone OKF parsing.
- `backend/repositories/{corpus,sources,retrieval,vectors}.py`: batch writes/counts, lexical search, optional embedding persistence and dormant vector-search interface.
- `backend/db/{models,session}.py`: async SQLAlchemy/asyncpg; `backend/db/migrations/versions/000{1,2,3,4}_*.py`: additive schema history; `alembic.ini`: migration entry.
- `scripts/import_jsonl_to_db.py`: manual restartable legacy importer; `scripts/assign_legacy_owner.py`: explicit administrator assignment with default preview. JSONL is not production storage.
- `doc/`: ignored local corpus/uploads; `.local/postgres/`: ignored local runtime/data. Never commit them, secrets, generated builds, dependencies, or coverage.
- `scripts/run_pipeline.py`: isolated local OKF/chunk export, cache, force and benchmark; not production storage. Architecture: `docs/runtime-readiness.md` and `docs/ingestion-pipeline.md`. `CLAUDE.md` delegates to this file.

## Connector layer

- Architecture: external provider → direct native transport/connector → `RawSourceEnvelope` → generic source registration → existing inspection/parsing → canonical → OKF → chunking. Provider code ends at the envelope; never add provider API calls or credential handling downstream.
- Gmail uses direct REST/Google OAuth for history cursors and individual messages/attachments. Drive uses direct v3/Google OAuth for changes, shared-drive metadata and structured Office exports. GitHub uses a direct GitHub App for user/installation access intersection, selected read-only repositories and commit/blob identities. Slack uses direct user OAuth/Web API for authorized channels/history/replies and native rate behavior. Google Cloud managed options were evaluated; no managed transport or cloud connection is implemented. Native transport fits the existing Linux/Docker deployment without managed connection-node cost or extra infrastructure. Decisions, current official references and setup are in `docs/connectors.md`.
- `backend/schemas/connectors.py`: strict common contracts, selection/query bounds, private application ACL and envelope. `backend/connectors/{core,base,http,registry,config}.py`: interface, OAuth/refresh/download helpers, bounded fixed-provider HTTPS transport and registration. `{gmail,drive,github,slack}.py`: native behavior; `{events,subscriptions,rates,vault}.py`: signature/identity verification, optional watches, durable method rates and encrypted TokenVault.
- `backend/api/connectors.py`: owner-authenticated lifecycle/discovery/configure/status/sync/query APIs; separately verified public events. `backend/services/connectors.py`: Auth Broker, one-time hashed owner/provider state, encrypted PKCE, account upsert, local-first disconnect and safe audit. `connector_sync.py`: durable PostgreSQL jobs/checkpoints, page/resource steps, advisory locks, backoff/resume, cursor expiry recovery and periodic reconciliation. `connector_sources.py`: generic registration, version/hash reuse, tombstones and private asset cleanup.
- Native synchronization must remain resumable across process restarts. Drive stores separate user/shared-drive cursors before enumeration; never mix their token namespaces. GitHub pins branch heads, falls back to non-recursive subtree steps for truncated trees, handles empty repositories, and paginates commit/PR files within the provider's 3,000-file limit. Gmail fetches external MIME body parts even when attachment downloads are disabled and rechecks parent selection before downloading attachments. Slack applies date bounds to threads/events as well as history.
- `ConnectorAccount` and opaque credential references are separate from ingested sources. `TokenVault` stores encrypted tokens in owner-scoped credential rows, supports previous encryption keys and row-locked refresh/rotation/revoke. Never expose provider access/refresh tokens, signing keys or the vault key in frontend JavaScript, logs, envelopes, OKF, chunks, embeddings or prompts. Keep a recoverable vault key outside the DB; do not replace it without a rotation plan.
- `0004` adds nine connector tables, source external identity/availability and partial deduplication indexes. Manual uploads/imports use `(owner_id, file_hash)` only for non-connector sources; connector sources use owner/provider/external-account/resource identity. Updates reuse the same source and preserve unchanged vectors. All writers/importers must honor these predicates; do not restore global hash deduplication that merges distinct provider resources.
- Resource selection is owner-controlled but native provider probes remain authoritative. Keep least-privilege read scopes/installation permissions; never infer private Slack access or trust browser identities. All envelope access is private to the connecting application owner; native permissions are retained evidence, not an automatic cross-account/group grant.
- `backend/processing/pipeline/context.py` attaches generic context to canonical, concepts/OKF, and chunks. `backend/repositories/source_access.py` supplies SQL availability/connection/lease checks to keyword and dormant vector retrieval before reading content. Preserve owner filtering, explicit removal, credential-loss exclusion and expiring ACL leases. Failed scans must not renew access. Do not advertise instantaneous provider ACL mirroring.
- Prefer cursor/change APIs and unchanged-file/version reuse; reconciliation repairs missed events and deletes. Persist individual failures/checkpoints so one bad resource does not restart an account. Slack defaults to 60-second durable history/replies reservations with at most 15 results; only tune after verifying the actual app's limits. Bounded retries/rate delays must not become blocking long worker sleeps.
- Verify selected roots during long jobs and before final lease renewal. If a selected root disappears, disable the previous inventory and reconcile the remaining authorized selections; preserve the user's configuration with a visible warning. Signed permission-removal events cancel in-flight jobs before reconciliation so stale fetched responses cannot restore access. Slack user-token revocation affects only the matching OAuth user; GitHub installation removal affects its mapped repositories. Explicit token revocation wipes local credentials and cancels pending jobs.
- Only exact provider event POST routes at `/api/connectors/events/{provider}` bypass Clerk. Gmail verifies Google OIDC identity/audience/issuer/expiry, Drive verifies channel token/resource/expiry, GitHub and Slack verify raw signatures (Slack timestamp too). Preserve bounded bodies, receipt idempotency and authoritative asynchronous fetching. Events never grant access. Do not forward the gateway credential or trusted owner into public event ingress.
- Disconnect wipes local credentials, cancels jobs and excludes sources before remote cleanup; do not refresh a token over the network before local disabling. Preserve retain/soft-delete/purge policy semantics and visible remote-cleanup warnings. Reconnect does not automatically re-enable retained content. Purge applies to connector sources/artifacts/files, not independent saved-history snapshots.
- Purge also removes connector resource rows and private payloads in persisted job cursors. Same-provider HTTPS download redirects are bounded; never forward credentials across providers or to arbitrary redirect hosts. Connected Apps supports health checks, clearing selections, active-job polling and background status polling.
- UI: `src/app/connectors/page.tsx`, `src/components/connectors/`, `src/lib/{connectors,connector-contracts}.ts`, provider callback/event routes and `src/lib/server/connector-events.ts`. Reuse the existing gateway/shell/client; server-only redirects/credentials. Polling progress is fetched/queued, not embedded/indexed. Source availability must remain visible. Structured queries are bounded selected-resource interfaces with paginated counts; there is no full planner or total-count UI.
- Tests use mocked provider HTTP and disposable PostgreSQL, never real/paid credentials in CI. `backend/tests/test_{connectors,connector_adapters}.py`, frontend connector/gateway/proxy tests and `tests/browser/verify-connectors.mjs` cover implemented boundaries. Live provider consent/delivery, quotas and cloud approval require separate operator verification. Mark provider/format limitations truthfully.
- `backend/tests/test_connector_workflows.py` exercises all four native adapters through OAuth, discovery, configuration, initial/incremental/reconciliation jobs, the real parsing/canonical/OKF/chunk pipeline, private retrieval and purge. External HTTP is the mocked boundary; this is not live provider verification. Keep token rotation, selected-root loss, event revocation and in-flight cancellation regression coverage.
- Configuration: backend-only `CONNECTORS__*` values in `backend/.env` or Compose root `.env`; fixed public Next origin, vault key, native OAuth/App credentials, optional event identities/secrets and scheduling bounds. Events default disabled. Register the exact provider callback URLs listed in `docs/connectors.md`; never introduce browser-supplied redirect origins or public secret variables.

## Persistence and ingestion invariants

- PostgreSQL is authoritative for sources, documents, chunks, debug sessions, chat messages, saved solutions. Do not substitute in-memory/browser/JSON storage.
- UUID keys; source/document/chunk content hashes deduplicate. Composite chunk→document/source FK prevents cross-source linkage; DB cascades remove dependent records. Saved solutions are independent JSONB snapshots, not session FKs.
- Schema changes require a new reviewed Alembic migration and PostgreSQL upgrade verification; preserve data and a single migration head. Do not edit shared migrations, use `create_all()`, stamp over errors, or drop data for convenience.
- `POST /api/documents` returns **202 acceptance**. Worker sleeps 2 seconds between polling/processing iterations, uses transaction advisory locks, runs parsing through a spawned process with deadline/output bounds and Linux memory limits, writes in one transaction, recovers persisted pending jobs. Updates preserve previous valid artifacts on failure and reuse unchanged chunks/vectors.
- Preserve `uploaded → processing → chunked → ready_for_embedding`, optional `embedding → indexed`, and safe `failed` states. Embedding failures preserve keyword-ready chunks and require explicit retry. `chunked` is flushed within the final transaction, not guaranteed as an observable intermediate state.
- Preserve duplicate-upload cleanup and failed-source retry, rollback of all job documents/chunks, generated private paths, path containment/symlink checks, filename sanitization, 50 MiB limit, allowed extensions, and safe errors. Never execute content or treat it as agent instructions.
- Debug attachments: frontend allows up to 5 UTF-8 text/source files at 50,000 bytes/file; backend validates up to 5 nonblank text contents at 50,000 characters each, without an attachment-extension check. Document upload extensions are shared with `backend/processing/pipeline/inspection.py`; native Office, structured data, source code, logs, email and transcripts are supported. Scanned PDFs/images require configured OCR. Audio/video require a trusted installed adapter; no transcription model is configured. Never accept invalid canonical documents/OKF/chunks or invent model output; unknown quality measurements remain null.

## Authentication and truthful capability boundaries

- Clerk protects matched frontend routes; signed-out pages redirect to `/sign-up`, frontend API requests return 401. Account routes and `/__clerk` are exempt; account pages redirect signed-in users to `/`.
- Next verifies Clerk sessions and overwrites identity/credentials with a server-only `FIXFLOW_API_TOKEN` plus `X-FixFlow-User-Id`. FastAPI fails closed without that token, verifies the trusted gateway credential, and scopes source/session/chat/saved/retrieval access to the account. CORS is not authorization. Keep FastAPI private; this is trusted server identity delegation, not direct Clerk JWT verification. No roles/admin UI exist. Never accept browser-supplied identities or expose the gateway secret. Legacy/imported records remain `__legacy__` until an administrator explicitly assigns them; never automatically grant all users access.
- Default provider has `generation="disabled"`, null confidence, no code fix; technologies are user-selected, not model-detected. Follow-up searches question text; default provider does not use saved history to generate an answer.
- Retrieval uses PostgreSQL `simple` full-text OR query, GIN index, `ts_rank`; no vector product retrieval, reranking, expansion, prompts, LLM diagnosis calls or agents. Debug-form repository references are stored, not fetched; selected authorized GitHub App repositories are fetched through connectors. Remote URL registration records `failed`; UI remote controls are disabled.
- Optional `HttpEmbeddingProvider` runs only with deliberately configured HTTPS endpoint/model/dimension; validates batch indexes and finite nonzero dimensions, times out, and commits source vectors atomically. Vectors remain NULL when unset. Nullable dimension-flexible storage retains per-row metadata constraints. Keyword retrieval remains unchanged. Do not label unembedded sources `Indexed` or fabricate similarity/confidence/fixes.

## Environment and commands

Run from repository root. Prefer existing `myenev` and `npm ci`; never reveal private environment values.

Configure credentials and start a database before applying migrations. Run Uvicorn and Next dev in separate terminals after setup.

```bash
npm ci
python3 -m venv myenev                    # only if absent
myenev/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env.local               # native frontend; only if absent
cp backend/.env.example backend/.env     # native backend; only if absent
myenev/bin/alembic upgrade head
myenev/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
npm run dev
```

- Frontend: `NEXT_PUBLIC_API_URL`, Clerk publishable key and account redirect variables; `CLERK_SECRET_KEY` stays server-only. `INTERNAL_API_URL` is the preferred server gateway target; `NEXT_PUBLIC_API_URL` is its legacy fallback, not a direct browser API destination. Set the same server-only `FIXFLOW_API_TOKEN` in `.env.local` and `backend/.env` (or root `.env` for Compose).
- Backend `Settings` reads root `.env`, then `backend/.env` (process environment overrides). `DATABASE_URL` takes precedence over `POSTGRES_*`; PostgreSQL only. Relative `FIXFLOW_DATA_DIR` resolves against `backend/`.
- Validated `FRONTEND_ORIGINS`: explicit HTTP(S) origins; no wildcard/credentials. Pool capacity must exceed ingestion worker count, or twice that count when embeddings are enabled; overlap must be smaller than chunk size. Leave `EMBEDDING_API_URL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, and `EMBEDDING_API_KEY` unset until deliberately configured. See the endpoint contract in `docs/runtime-readiness.md`.
- Full Docker: copy `.env.example` to root `.env` only if absent, fill required keys, then `docker compose up -d`. `db` healthy → `migrate` succeeds → `backend` healthy → `frontend`. Browser requests use the Next gateway; internal backend hostname is server-only.
- Database-only: `docker compose up -d --wait db` with configured root `.env`. Native backend uses host/port, not `db:5432`.
- `docker compose ps`, `docker compose logs -f`, `docker compose down`. **Never `down -v` without explicit authorization to erase DB/uploads.** Public frontend variables are build-time; rebuild after changes. Changing password env does not rotate existing DB credentials.
- Initialized native cluster only: `bash scripts/local_postgres.sh start`, `bash scripts/local_postgres.sh status`, or `bash scripts/local_postgres.sh stop` (Unix socket, port 55432, no TCP listener); script does not initialize a new cluster.

## Verification and change safety

1. Inspect `git status` and `git diff`; preserve unrelated changes. Read relevant implementation, callers, contracts, tests, settings and migrations before editing.
2. Keep diffs focused and architecture intact; reuse existing helpers/components. No unrelated upgrades, framework/state-library replacements, rewrites or reorganizations.
3. Use typed async Python and explicit TypeScript domain types; no blocking I/O in async request paths, convenience `any`, swallowed errors, global rule suppression, or raw untrusted SQL. Preserve safe error envelopes and secret-free logs.
4. Add regression coverage for meaningful bugs; run targeted tests then relevant broader gates. Never weaken tests or fake production behavior to pass them.

```bash
npm run lint
npm run typecheck
npm test
npm run test:coverage
npm run build
myenev/bin/python -m ruff check backend scripts
myenev/bin/python -m mypy backend scripts
myenev/bin/python -m pytest
myenev/bin/alembic check                 # DB/schema work
myenev/bin/python -m bandit -r backend scripts -x backend/tests,scripts/tests -q # production security scan
myenev/bin/python -m pip_audit -r requirements-dev.lock.txt # locked dependency audit
```

- `TEST_DATABASE_URL` must point to an explicitly disposable database ending `_test`: fixtures downgrade/upgrade and truncate tables. Without it DB integration tests skip. Never use application data.
- Frontend tests: `tests/frontend/`; Python: `backend/tests/`. Browser harness: `tests/browser/` writes ignored output to `.local/browser-evidence/`; mocked Clerk/Next routing does not verify live sign-in. For connectors, start `node_modules/.bin/vite --config tests/browser/vite.config.mts`, then run `FIXFLOW_BROWSER_TOOLS=/path/to/installed/playwright node tests/browser/verify-connectors.mjs`. Playwright is external local test tooling, not a runtime dependency.
- Coverage: `coverage/frontend/lcov.info`, `coverage/python-coverage.xml`; keep Sonar source/test mappings intact. CI runs both coverage suites and Sonar, not every local gate.

Review the final diff for accidental edits, unsafe logging, secrets, missing tests and false capability claims. Report exactly which checks ran/passed/skipped/failed; do not claim external services, migrations or live auth were verified without execution.

Careful-change chains: API/types/schemas → every screen and persisted JSONB; processing/corpus → live worker + offline tools/importer; settings/session → API, worker, migrations, health; statuses → DB constraints, worker, search eligibility, polling/UI; provider/store → sessions, chat, history, saved findings. Maintain these consumers together.

Priority: explicit task requirements → security/data integrity → repository rules → tested conventions → minimal changes. Never interpret a convenience preference as permission to weaken security, corrupt data or misrepresent behavior. Do not force-push, reset shared history, delete branches, amend others’ work, or merge shared branches without explicit authorization.
