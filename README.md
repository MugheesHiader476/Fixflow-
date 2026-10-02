# FixFlow

Next.js + FastAPI with PostgreSQL as the primary persistent store. Files → Documents → Chunks → PostgreSQL/pgvector. Embeddings remain NULL until an HTTPS embedding endpoint, model, and dimension are configured. Retrieval is keyword-based; no diagnosis model or reranker is connected. See the [runtime audit and setup](docs/runtime-readiness.md).

The Sources page accepts ordinary documents and standalone OKF Markdown concepts. Explicit OKF mode validates concept frontmatter before the content enters the searchable corpus.

## Requirements

- Node.js 20.9+ and Python 3.11+
- PostgreSQL 18 with pgvector (or Docker Compose)
- Clerk keys for the existing sign-in UI

## Dependencies and environment

```bash
npm ci
python3 -m venv myenev
myenev/bin/python -m pip install -r requirements-dev.txt
```

Keep your existing environment files. For a fresh checkout, copy `.env.example` to `.env.local`, add your Clerk keys, and set `NEXT_PUBLIC_API_URL=http://localhost:8000`. Copy `backend/.env.example` to `backend/.env` and set your own `POSTGRES_PASSWORD` (generate one with `openssl rand -hex 32`). Generate one separate random `FIXFLOW_API_TOKEN` (at least 32 ASCII characters) and set the same value in `.env.local` and `backend/.env`. This gateway credential stays server-side. Do not commit credentials.

The backend and Alembic automatically read `backend/.env`; shell variables take precedence. `DATABASE_URL`, when set, overrides the separate `POSTGRES_*` fields. Do not put database credentials in `NEXT_PUBLIC_*` variables. Relative `FIXFLOW_DATA_DIR` paths remain relative to `backend/`. `FIXFLOW_PYTHON` is no longer needed: ingestion runs in the backend's own virtual environment, without subprocesses.

## Docker Compose (complete application)

For a fresh Docker setup, copy `.env.example` to a root `.env`. Set a random server-only `FIXFLOW_API_TOKEN` (at least 32 ASCII characters), a nonempty `POSTGRES_PASSWORD`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, and `CLERK_SECRET_KEY` from your own Clerk application. Keep `.env` private. Compose reads this file automatically; the existing `backend/.env` and `.env.local` remain for native development and are not used by the containers. If you already have Clerk values in `.env.local`, copy those values into the root `.env`. `INTERNAL_API_URL` is the server gateway target; native development falls back to `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`); `FRONTEND_ORIGINS` must include the frontend browser origin.

```bash
cp .env.example .env
# Edit .env and fill the required credentials.
docker compose up --build
```

The frontend is at http://localhost:3000 and the backend health endpoint is at http://localhost:8000/health. The browser calls the same-origin Next.js `/api/backend` gateway; the gateway uses `http://backend:8000` inside Compose. The backend connects to `db:5432`. If host port 5432 is occupied, change `POSTGRES_PORT` in the root `.env`; container-to-container traffic still uses `db:5432`. A one-shot `migrate` service runs `alembic upgrade head` after PostgreSQL becomes healthy; the API starts only after migrations succeed. The database and uploaded documents use separate named volumes. This Compose setup binds locally and runs one backend instance; authenticated account records are isolated through the trusted Next gateway. Public hosting still requires operational protections listed in the runtime audit.

| Action | Command |
| --- | --- |
| Start in background | `docker compose up -d --build` |
| Status | `docker compose ps` |
| Logs | `docker compose logs -f` |
| Stop | `docker compose down` |
| Rebuild | `docker compose build --no-cache` |

`docker compose down` keeps the PostgreSQL and upload volumes. **`docker compose down -v` deletes the volumes and local database data.** Do not use it for normal shutdown. Browser-facing `NEXT_PUBLIC_*` variables are fixed at frontend build time, so rebuild after changing them. The `POSTGRES_PASSWORD` value initializes a new database volume; changing it later does not change credentials inside an existing volume.

For database-only native frontend/backend development, use `docker compose up -d --wait db` with the same root `.env`, then point `backend/.env` at `127.0.0.1` and run Alembic locally.

### Native Linux (this workspace)

A PostgreSQL 18/pgvector runtime and initialized cluster are installed under ignored `.local/postgres/`; the private `backend/.env` points to this cluster. It uses Unix-socket peer authentication and exposes no TCP listener.

```bash
bash scripts/local_postgres.sh start
myenev/bin/alembic upgrade head
```

`bash scripts/local_postgres.sh status` checks the server; `stop` stops it without deleting data. Back up `.local/postgres/data` using PostgreSQL backup tools. On another machine, use Docker above or install PostgreSQL/pgvector and initialize a native cluster with `initdb` before using this helper. The ignored runtime/data are not distributed with Git.

For managed PostgreSQL, set `DATABASE_URL` to your provider's PostgreSQL connection URL. The migration role needs permission to install the vector extension; provision that permission with your database administrator. Use a separate least-privileged application role in production.

## Start

```bash
myenev/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# In another terminal:
npm run dev
```

Frontend: http://localhost:3000. Health: http://localhost:8000/health. API documentation: http://localhost:8000/docs. Health checks database connectivity, pgvector, required tables, and the Alembic revision. Public health omits corpus counts; authenticated `/api/readiness` reports counts for the current account and configuration indicators. Missing migrations or unavailable dependencies return HTTP 503 without exposing credentials or internal errors. Settings displays this readiness information.

## Account entry

Signed-out visits to the frontend workspace open `/sign-up` first. Existing users can switch to `/sign-in`; Clerk handles account field validation and verification. After sign-up or sign-in, users return to the workspace. Frontend pages and all Next.js gateway routes require a Clerk session. The gateway forwards a server-only credential and verified account identity; FastAPI verifies that credential and scopes persisted records and retrieval to the account. Missing authentication configuration fails closed. Keep FastAPI private; browsers must use the gateway. See the runtime audit for legacy-data assignment and deployment limitations.

## Ingestion and persistence

`POST /api/documents` accepts the existing multipart fields (`kind`, `value`, `content`, `file`, optional `ingestion_format=document|okf`) and returns HTTP 202 with `source_id`, `id`, and status. Optional `force=true` reprocesses a duplicate; `update_source_id` replaces an owned source with an incremental version. Uploads are streamed into private server-generated paths, validated by extension and content and capped at 50 MiB. Text must be nonblank UTF-8 without null bytes; Office archive expansion and extracted-text/chunk counts are bounded. Explicit OKF mode accepts standalone Markdown concepts and rejects invalid frontmatter rather than falling back to plain text. The response is acceptance, not completed ingestion.

A database-backed worker runs content inspection, native/layout/optional OCR parsing, measured quality gates, a validated canonical document, grounded OKF concepts, and structure-aware validated chunks in a killable process. PostgreSQL stores the complete pipeline artifact and compatible document/chunk projections transactionally, then transitions `uploaded → processing → chunked → ready_for_embedding`. Failures roll back the attempt and preserve previous valid artifacts on updates. Pending jobs survive restarts. Advisory locks coordinate concurrent workers. Successful duplicate uploads reuse existing output; failed uploads retry the same source. Updates reparse that source and reuse unchanged concepts/chunks, preserving unrelated sources and unchanged vectors. See [the ingestion pipeline](docs/ingestion-pipeline.md) for schemas, adapters, token budgets, safety limits and CLI export.

`GET /api/sources`, `GET /api/sources/{id}`, and `GET /api/sources/{id}/status` return persisted counts/status/errors. Existing debug sessions, chats, and saved solutions are persistent too. No delete HTTP route existed previously; the repository supports cascading deletion for administrative use. Existing follow-up keyword retrieval uses PostgreSQL full-text search, not fabricated vector similarity.

Remote URL registration remains available but reports a clear failed/not-configured state: there is no remote fetcher. Scanned PDFs/images require configured OCR; Docker includes Tesseract/Poppler and enables its conditional fallback. Native development defaults to OCR disabled. Audio/video require an installed transcription adapter. Account isolation is enforced through the private trusted gateway; no public direct-token API or admin roles are provided.

The Knowledge Sources page polls pending jobs, retries transient polling failures, and shows “Ready for embedding,” never “Indexed” before vectors exist. File uploads preserve the original file; pasted text is submitted as a separate input mode. Remote URL controls are disabled until a fetcher is implemented.

## Debugging and AI integration

The current implementation is **documentation retrieval, not AI diagnosis**. Both diagnosis and follow-up questions search real PostgreSQL document chunks using keyword retrieval. With no model connected, the UI reports no assessed confidence, does not invent code fixes, and labels retrieved evidence honestly. Upload documentation first: an empty database is structurally ready but cannot provide retrieval evidence.

Debug inputs (error, code, context, repository reference, technologies, and text attachments) are persisted with each session. Attachments are limited to five UTF-8 text/source files, 50 KB each. Repository references are saved, not fetched. Reopening a session restores its inputs and conversation via `GET /api/sessions/{id}` and `GET /api/sessions/{id}/messages`. History, saved details, Markdown export, theme selection, and request error/retry states are supported.

`backend/services/diagnosis.py` currently uses `DocumentationProvider`. It returns matching documentation without a model-generated answer. No paid API calls are made by the test suite.

### Authentication troubleshooting

If the browser loops before the workspace renders and the server logs a Clerk handshake/session redirect warning, check connectivity to your Clerk instance and verify that the publishable and secret keys belong to the same instance. Do not disable authentication or paste secret keys into logs to investigate. A frontend build or mocked component test does not validate live Clerk sign-in. The gateway requires matching server-only tokens in Next and FastAPI; missing or mismatched tokens return safe errors.

### Optional legacy import

No existing JSONL is imported or modified automatically. Imported and pre-migration records remain under `__legacy__`, inaccessible to browser accounts. An administrator can preview and explicitly assign them using `python -m scripts.assign_legacy_owner --owner user_VERIFIED_ID`, then repeat with `--apply` after reviewing the destination; conflicting account hashes abort assignment without deleting data.

```bash
myenev/bin/python -m scripts.import_jsonl_to_db \
  --documents doc/processed/documents.jsonl \
  --chunks doc/processed/chunks.jsonl --batch-size 100
```

The importer streams records, skips malformed lines, preserves metadata, reports progress, and deduplicates across reruns. Either input may be omitted. Documents-only imports generate chunks; chunks-only imports retain fragments as documents with `imported_from_chunk=true` metadata because original pages cannot be reconstructed. Each batch is transactional and restartable. Sources without usable chunks remain failed with an explanatory error.

## Optional embedding pipeline

`backend/repositories/vectors.py` provides `insert_embeddings()`, `similarity_search()`, `delete_source_vectors()`, and `count_embedded_chunks()`. Mutating calls use the caller's transaction. The optional worker in `backend/services/embeddings.py` uses this repository to persist real endpoint responses; no vectors are generated while configuration is unset.

Leave `EMBEDDING_API_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_DIM`, and `EMBEDDING_MODEL` unset by default. The migration uses an unconstrained nullable vector column; when a real model is selected, configure both centrally and add a dimension-specific migration/index as appropriate. The repository validates finite, nonzero vectors and dimensions. Search without configured/populated embeddings raises “Embedding pipeline not configured.” Deterministic vectors exist only in tests. See [runtime readiness](docs/runtime-readiness.md) for the endpoint protocol, failure/retry states, and model-change restrictions. Vector search is not connected to the product retrieval path.

## Quality checks

Database tests require an explicitly disposable database whose name ends in `_test`; they run Alembic downgrade/upgrade and reset its tables. Never point `TEST_DATABASE_URL` at application data.

For this native workspace:

```bash
export TEST_DATABASE_URL="postgresql+asyncpg://$(id -un)@/fixflow_test?host=$(pwd)/.local/postgres&port=55432"
myenev/bin/python -m pytest
```

For Docker, create the disposable database first (once):

```bash
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" fixflow_test'
```

Then set `TEST_DATABASE_URL` to that database's connection URL using your configured credentials. Missing `TEST_DATABASE_URL` skips DB integration tests rather than touching application data.

```bash
myenev/bin/alembic check
myenev/bin/python -m ruff check backend scripts
myenev/bin/python -m mypy backend scripts
myenev/bin/python -m pytest
npm run lint
npm run typecheck
npm run test:coverage
npm run build
```

Coverage: `coverage/frontend/lcov.info` and `coverage/python-coverage.xml`; both are wired to `sonar-project.properties`. The integration suite verifies migrations, pgvector, deduplication, partial-failure rollback, retries, cascades, API health, restart recovery and canonical pipeline persistence. Golden fixtures cover native document/table/code formats, column order and real optional OCR. The retained legacy JSONL loader emits a LangChain Community deprecation warning; live ingestion uses the new adapters.

After producing coverage:

```bash
sonar-scanner -Dsonar.host.url="$SONAR_HOST_URL" -Dsonar.token="$SONAR_TOKEN"
```
