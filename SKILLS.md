# Skills Required for FixFlow

Active implementations and repository-defined verification tools are listed; tools offered for local checks are distinguished from CI execution. No LLM diagnosis, reranker, prompt system, agent framework, or admin UI is implemented. Optional HTTP embeddings and trusted gateway account isolation are implemented; embeddings remain disabled until configured.

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
| Logging / readiness | Server failure logs use exception types plus request/source identifiers; read-only DB/vector/schema/revision/count probes | `backend/main.py`, `backend/services/{ingestion,readiness}.py` |

Development and verification commands, architectural constraints, and data-safety rules are in `AGENTS.md`; the current system audit is in `docs/runtime-readiness.md`.
