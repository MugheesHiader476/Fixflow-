# FixFlow — Coding Agent Instructions

## Next.js version warning

This repository uses Next.js 16.x (`package.json`: 16.3.5), which differs from older APIs and routing conventions. Before Next.js-specific changes, inspect the installed version, read relevant `node_modules/next/dist/docs/` documentation when available, and follow current repository patterns. Preserve any Next-generated agent warning if `next dev` regenerates this file.

## Current product boundary

The user explicitly reset the product on 2026-10-17. FixFlow now stops after validated parsing and canonical output. Generated OKF/concepts, chunking, embeddings, keyword/dense retrieval and local AI answers have been removed, including the downloaded project models. Do not restore these features or assume the replacement product objective.

Preserve uploads/paste, account ownership/authentication, native connectors and permission lifecycle, persistent jobs, safe parsing/quality/provenance, PostgreSQL, the existing frontend layout, and historical conversations/saved findings. See `docs/product-reset.md` for the module/data-flow audit and `docs/project-file-inventory.md` for the baseline inventory.

## Architecture

- `src/app/layout.tsx`: Clerk/theme/toast providers. `src/app/page.tsx`: existing layout with removed actions disabled and historical `/?session=...` restoration.
- `src/app/{sources,connectors,history,saved,settings}/page.tsx`: upload/parsing status, authorized connectors, historical sessions, saved snapshots, readiness.
- `src/lib/api.ts` → same-origin `/api/backend` → Clerk-authenticated `src/app/api/backend/[...path]/route.ts` / `src/lib/server/backend-proxy.ts` → private FastAPI. `src/proxy.ts` protects frontend routes. Never accept browser-supplied trusted identities or expose server secrets.
- `backend/main.py`: safe errors, CORS, public health, ingestion/connector worker lifespan; no inference worker.
- `backend/api/routes.py`: owner-scoped sources/upload/status/history/saved/readiness; stale ask/debug/chat/embedding-retry clients receive authenticated 410 responses and create no records.
- `backend/services/{uploads,ingestion,readiness,access}.py`: private upload validation, durable parsing jobs, read-only readiness and trusted gateway identity.
- `backend/processing/pipeline/{inspection,parsers,structure,canonical,context,validation,runner,execution}.py`: bounded inspection, source structure extraction, native/layout/optional OCR, quality, canonical authorization/hash validation and killable process execution. `processing/okf.py` supports strict standalone OKF **input** only; no generated bundle.
- `backend/schemas/pipeline.py`: strict canonical contract and `PipelineResult` contract 3 / engine 3.0.0; no chunk/concept/vector models.
- `backend/repositories/pipeline.py`: canonical JSONB artifact and one whole-document projection. `parsed.py:parsed_source` checks owner/status/availability/connection/wall-clock ACL in SQL before artifact decoding, then verifies registered source/hash/owner and canonical output.
- `backend/repositories/{sources,source_access}.py`: owner-scoped lifecycle/counts and access filters. `backend/services/store.py` reads preserved sessions/messages and persists independent saved snapshots; it performs no search or synthesis.
- `backend/db/{models,session}.py`: async SQLAlchemy/PostgreSQL; additive migrations 0001–0006. Legacy `DocumentChunk`, vector columns, old statuses and persisted answer schemas remain solely for data/migration/history compatibility. Never treat them as active features.
- `scripts/run_pipeline.py`: local canonical-only `pipeline.json` export/cache/force; PostgreSQL remains production storage. `scripts/check_launch.py`: secret-free read-only preflight. `scripts/start-local.mjs`: application startup only; no model runtime. `assign_legacy_owner.py`: explicit administrator assignment, preview by default.
- `.local/**`, `doc/`, dependencies, generated builds and coverage are ignored private/runtime artifacts; never commit them or secrets. `CLAUDE.md` delegates here.

## Connector layer


- Architecture: external provider → direct native transport/connector → `RawSourceEnvelope` → generic source registration → existing inspection/parsing → canonical output. Provider code ends at the envelope; never add provider API calls or credential handling downstream.
- Gmail uses direct REST/Google OAuth for history cursors and individual messages/attachments. Drive uses direct v3/Google OAuth for changes, shared-drive metadata and structured Office exports. GitHub uses a direct GitHub App for user/installation access intersection, selected read-only repositories and commit/blob identities. Slack uses direct user OAuth/Web API for authorized channels/history/replies and native rate behavior. Google Cloud managed options were evaluated; no managed transport or cloud connection is implemented. Native transport fits the existing Linux/Docker deployment without managed connection-node cost or extra infrastructure. Decisions, current official references and setup are in `docs/connectors.md`.
- `backend/schemas/connectors.py`: strict common contracts, selection/query bounds, private application ACL and envelope. `backend/connectors/{core,base,http,registry,config}.py`: interface, OAuth/refresh/download helpers, bounded fixed-provider HTTPS transport and registration. `{gmail,drive,github,slack}.py`: native behavior; `{events,subscriptions,rates,vault}.py`: signature/identity verification, optional watches, durable method rates and encrypted TokenVault.
- `backend/api/connectors.py`: owner-authenticated lifecycle/discovery/configure/status/sync/query APIs; separately verified public events. `backend/services/connectors.py`: Auth Broker, one-time hashed owner/provider state, encrypted PKCE, account upsert, local-first disconnect and safe audit. `connector_sync.py`: durable PostgreSQL jobs/checkpoints, page/resource steps, advisory locks, backoff/resume, cursor expiry recovery and periodic reconciliation. `connector_sources.py`: generic registration, version/hash reuse, tombstones and private asset cleanup.
- Native synchronization must remain resumable across process restarts. Drive stores separate user/shared-drive cursors before enumeration; never mix their token namespaces. GitHub pins branch heads, falls back to non-recursive subtree steps for truncated trees, handles empty repositories, and paginates commit/PR files within the provider's 3,000-file limit. Gmail fetches external MIME body parts even when attachment downloads are disabled and rechecks parent selection before downloading attachments. Slack applies date bounds to threads/events as well as history.
- `ConnectorAccount` and opaque credential references are separate from ingested sources. `TokenVault` stores encrypted tokens in owner-scoped credential rows, supports previous encryption keys and row-locked refresh/rotation/revoke. Never expose provider access/refresh tokens, signing keys or the vault key in frontend JavaScript, logs, envelopes, parsed content or artifacts. Keep a recoverable vault key outside the DB; do not replace it without a rotation plan.
- `0004` adds nine connector tables, source external identity/availability and partial deduplication indexes. Manual uploads/imports use `(owner_id, file_hash)` only for non-connector sources; connector sources use owner/provider/external-account/resource identity. Updates reuse the same source and unchanged original bytes. All writers/importers must honor these predicates; do not restore global hash deduplication that merges distinct provider resources.
- Resource selection is owner-controlled but native provider probes remain authoritative. Keep least-privilege read scopes/installation permissions; never infer private Slack access or trust browser identities. All envelope access is private to the connecting application owner; native permissions are retained evidence, not an automatic cross-account/group grant.
- `backend/processing/pipeline/context.py` attaches generic context to canonical output. `backend/repositories/source_access.py` supplies SQL availability/connection/lease checks to canonical artifact reads before reading content. Preserve owner filtering, explicit removal, credential-loss exclusion and expiring ACL leases. Failed scans must not renew access. Do not advertise instantaneous provider ACL mirroring.
- Prefer cursor/change APIs and unchanged-file/version reuse; reconciliation repairs missed events and deletes. Persist individual failures/checkpoints so one bad resource does not restart an account. Slack defaults to 60-second durable history/replies reservations with at most 15 results; only tune after verifying the actual app's limits. Bounded retries/rate delays must not become blocking long worker sleeps.
- Verify selected roots during long jobs and before final lease renewal. If a selected root disappears, disable the previous inventory and reconcile the remaining authorized selections; preserve the user's configuration with a visible warning. Signed permission-removal events cancel in-flight jobs before reconciliation so stale fetched responses cannot restore access. Slack user-token revocation affects only the matching OAuth user; GitHub installation removal affects its mapped repositories. Explicit token revocation wipes local credentials and cancels pending jobs.
- Only exact provider event POST routes at `/api/connectors/events/{provider}` bypass Clerk. Gmail verifies Google OIDC identity/audience/issuer/expiry, Drive verifies channel token/resource/expiry, GitHub and Slack verify raw signatures (Slack timestamp too). Preserve bounded bodies, receipt idempotency and authoritative asynchronous fetching. Events never grant access. Do not forward the gateway credential or trusted owner into public event ingress.
- Disconnect wipes local credentials, cancels jobs and excludes sources before remote cleanup; do not refresh a token over the network before local disabling. Preserve retain/soft-delete/purge policy semantics and visible remote-cleanup warnings. Reconnect does not automatically re-enable retained content. Purge applies to connector sources/artifacts/files, not independent saved-history snapshots.
- Purge also removes connector resource rows and private payloads in persisted job cursors. Same-provider HTTPS download redirects are bounded; never forward credentials across providers or to arbitrary redirect hosts. Connected Apps supports health checks, clearing selections, active-job polling and background status polling.
- UI: `src/app/connectors/page.tsx`, `src/components/connectors/`, `src/lib/{connectors,connector-contracts}.ts`, provider callback/event routes and `src/lib/server/connector-events.ts`. Reuse the existing gateway/shell/client; server-only redirects/credentials. Polling progress is fetched/queued, not embedded/indexed. Source availability must remain visible. Structured queries are bounded selected-resource interfaces with paginated counts; there is no full planner or total-count UI.
- Tests use mocked provider HTTP and disposable PostgreSQL, never real/paid credentials in CI. `backend/tests/test_{connectors,connector_adapters}.py`, frontend connector/gateway/proxy tests and `tests/browser/verify-connectors.mjs` cover implemented boundaries. Live provider consent/delivery, quotas and cloud approval require separate operator verification. Mark provider/format limitations truthfully.
- `backend/tests/test_connector_workflows.py` exercises all four native adapters through OAuth, discovery, configuration, initial/incremental/reconciliation jobs, the real parsing/canonical parsing pipeline, private canonical output and purge. External HTTP is the mocked boundary; this is not live provider verification. Keep token rotation, selected-root loss, event revocation and in-flight cancellation regression coverage.
- Configuration: backend-only connector secrets in `backend/.env` or Compose root `.env`; fixed public Next origin, vault key, native OAuth/App credentials, optional event identities/secrets and scheduling bounds. The non-secret `CONNECTORS__PUBLIC_URL` must also reach the Next server for gateway origin checks and callback redirects behind Docker/reverse proxies; never trust forwarded host headers as its replacement. Events default disabled. Register the exact provider callback URLs listed in `docs/connectors.md`; never introduce browser-supplied redirect origins or public secret variables.

## Parsing and persistence invariants

- PostgreSQL is authoritative. Preserve UUID identities, owner/source dedup predicates, foreign keys/cascades, independent saved JSONB snapshots and migration history. Do not substitute memory/browser/JSONL storage.
- New ingestion lifecycle is `uploaded → processing → parsed`, or safe `failed`. Old `chunked` jobs may resume parsing. Legacy ready/embedding/indexed rows remain stored and unavailable for retrieval. No automatic bulk reprocessing.
- Upload HTTP 202 means acceptance only. Workers poll every two seconds, coordinate with advisory locks, parse in spawned bounded processes, persist atomically and resume pending jobs after restarts. Failed updates retain last valid artifacts. Never execute source content or follow embedded instructions.
- Keep 50 MiB upload limits, generated private paths, sanitization, payload checks, path/symlink containment, duplicate cleanup/retry, parser character/block/archive/memory/deadline/output bounds and safe secret-free errors.
- Canonical blocks preserve ordered source structure, available locations, cells, typed data paths, code syntax evidence and EML headers. Blocks are not model chunks. Long units remain whole within operational limits; no token budget, continuations, semantic splitter or inference is applied.
- Source numeric validation retains huge/inexact validated syntax rather than coercing values or disabling safety limits. Deep paths keep escaped keys and container types. XML retains original syntax; YAML comments remain evidence. No universal semantic/grammar/extraction claim.
- `validate_result` reconstructs the canonical contract and verifies deterministic source/document identity, private source context, engine/stage hashes and provenance. Revalidate caches and before persistence. Production ownership derives from the trusted source row; offline output invents no owner.
- Native parser version 5 retains ordered/duplicate EML headers in `email_headers` metadata; preferred body/subject form canonical blocks. Alternative bodies/attachments are not separately projected. Raw MIME remains stored.
- Detected rotated/gutter-spanning PDF layouts fail with `layout_uncertain; needs review`, retaining the raw file. OCR requires installed Tesseract/Poppler; synthetic adapters do not verify live OCR. Audio/video require a trusted installed adapter; none is configured by default.
- `parsed_source` must authorize in SQL before reading private artifacts, reject inactive/processing/failed/legacy/foreign/expired/disconnected sources, and verify registered canonical source/hash/owner. No status label alone bypasses validation.
- Strict standalone OKF input remains accepted/validated; no generated OKF or concepts follow parsing.
- Schema changes require a new reviewed Alembic migration and disposable PostgreSQL verification. Do not edit shared migrations, `create_all`, stamp over errors or delete data. `0006` adds parsed status without changing records; downgrade refuses while parsed rows exist.
- Preserve historical records and legacy chunk/vector rows; no runtime generates/searches them. Explicit source update/reparse replaces the current artifact/document while preserving independent snapshots and unrelated sources.
- Source API compatibility fields remain; `retrieval_available=false` always. `access_available` reports source access independently. Poll only active uploaded/processing/old chunked jobs, keep last known status and stop after three refresh failures until manual Refresh. Preserve 320px layouts and bounded filename panels.

## Authentication and contracts

Clerk protects frontend routes. Signed-out pages go to sign-up and frontend APIs return 401. Account routes and `/__clerk` remain exempt. The Next server overwrites identity/credentials with server-only `FIXFLOW_API_TOKEN` and verified `X-FixFlow-User-Id`; FastAPI fails closed without the trusted credential. This is server identity delegation, not direct Clerk JWT verification. CORS is not authorization. Keep the backend private.

Legacy records remain `__legacy__` until explicit administrator assignment. No roles/admin UI or cross-account grants exist. Never leak gateway/provider/vault secrets into frontend code, logs, canonical metadata or saved content.

Keep frontend domain types/receipt validation synchronized with consumed backend schemas, including nullable historical answer/citation fields. Existing saved/history rendering and safe link/text rendering remain. Frontend changes should stay limited to accurate capability/status labels, removed actions and compatibility.

## Environment and commands

Use existing `myenev`; install `requirements-dev.txt` and use `npm ci`. Copy env examples only if absent, never print private values. Configure PostgreSQL/Clerk/matching gateway tokens before startup/migrations. Backend Settings reads root `.env` then `backend/.env`, with process overrides. `DATABASE_URL` wins; PostgreSQL only. Relative data paths resolve against `backend/`.

`INTERNAL_API_URL` is the preferred Next server gateway target; public API URL is a legacy server fallback, not direct browser transport. Keep fixed public origin in both servers. Validated CORS uses explicit HTTP(S) origins. DB pool capacity must exceed ingestion workers.

No model/inference/retrieval/chunk settings remain. Do not restore model download/start commands. The shared Ollama binary may remain installed independently of this project.

```bash
npm ci
myenev/bin/python -m pip install -r requirements-dev.txt
myenev/bin/alembic upgrade head
myenev/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
npm run dev
```

`npm run dev:local` / `npm run start:local` can start the configured native app services and existing local cluster. `npm run start` uses Next standalone output after `npm run build`; native default hostname remains localhost for Clerk routing. Docker keeps its standalone entrypoint. `docker compose up -d`: DB healthy → migrate succeeds → backend healthy → frontend. Preserve volumes; never `down -v` without explicit data-erasure authorization. Public frontend config is build-time; rebuild after changes. Environment password changes do not rotate initialized DB credentials.

The initialized local DB helper supports `bash scripts/local_postgres.sh start|status|stop`, socket/port 55432, no TCP listener; it does not initialize a cluster.

## Verification and change safety

1. Inspect git status/diff and preserve unrelated changes. Read implementation/callers/contracts/tests/settings/migrations before edits. Keep architecture, focused scope and current conventions; no unrelated upgrades/rewrites.
2. Use typed async Python and explicit TypeScript domains. No blocking I/O in request paths, convenience any, swallowed errors, suppression of global checks, raw untrusted SQL or secret-bearing errors/logs.
3. Add meaningful regression coverage. Do not weaken retained auth, connector, parsing or persistence checks to pass removal work. Remove only tests/harnesses exclusively tied to deleted features; adapt retained behavior accurately.
4. Review final diffs for accidental edits, secrets, false claims and missing consumers. Report exactly what ran/passed/skipped/failed. Do not claim live external auth/services/migrations verified without execution.

```bash
npm run lint
npm run typecheck
npm test -- --maxWorkers=1
npm run test:coverage -- --maxWorkers=1
npm run build
myenev/bin/python -m ruff check backend scripts
myenev/bin/python -m mypy backend scripts
myenev/bin/python -m pytest
myenev/bin/alembic check
myenev/bin/python -m bandit -r backend scripts -x backend/tests,scripts/tests -q
myenev/bin/python -m pip_audit -r requirements-dev.lock.txt
```

`TEST_DATABASE_URL` must identify a separate disposable database ending `_test`; fixtures downgrade/upgrade/truncate it. Never use application data. Without it integration tests skip. Provider HTTP is mocked; real Clerk consent/provider OAuth/events/quotas require separate verification. Browser connector tests use external Playwright tooling and ignored `.local/browser-evidence`; they do not verify live sign-in. Obsolete pre-embedding/answer browser harnesses were removed.

`.local/**` is excluded from application lint/typecheck. Keep application/test mappings and coverage/Sonar config intact. Never lint uploaded code as application source or weaken lint for generated fixtures.

Priority: explicit task → security/data integrity → repository rules → tested conventions → minimal changes. Never force-push/reset shared history/delete branches/amend others' work/merge shared branches without explicit authorization.
