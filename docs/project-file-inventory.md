# Project file inventory

Baseline: 284 tracked files on 2026-10-17. This lists the complete initial source/configuration/test/document/asset inventory and whether each path remains. The companion [audit](product-reset.md) explains module behavior and data handoffs. Private environment values, uploaded data, dependencies, generated builds and database internals are excluded. Binary fixtures/assets are inventoried as such rather than treated as source text.

| Baseline path | Kind / responsibility | Reset disposition |
| --- | --- | --- |
| [.codeviz/Untitled 1/Unnamed-CodeViz-Diagram.png](../.codeviz/Untitled 1/Unnamed-CodeViz-Diagram.png) | Visual asset or historical design artifact (binary) | Retained |
| [.dockerignore](../.dockerignore) | Repository, runtime, dependency or build configuration | Retained |
| [.env.example](../.env.example) | Repository, runtime, dependency or build configuration | Retained |
| [.github/workflows/sonarqube.yml](../.github/workflows/sonarqube.yml) | Repository, runtime, dependency or build configuration | Retained |
| [.gitignore](../.gitignore) | Repository, runtime, dependency or build configuration | Retained |
| [.vscode/settings.json](../.vscode/settings.json) | Repository, runtime, dependency or build configuration | Retained |
| [AGENTS.md](../AGENTS.md) | Repository, runtime, dependency or build configuration | Retained |
| [CLAUDE.md](../CLAUDE.md) | Repository, runtime, dependency or build configuration | Retained |
| [Dockerfile.backend](../Dockerfile.backend) | Repository, runtime, dependency or build configuration | Retained |
| [Dockerfile.frontend](../Dockerfile.frontend) | Repository, runtime, dependency or build configuration | Retained |
| [README.md](../README.md) | Repository, runtime, dependency or build configuration | Retained |
| [SKILLS.md](../SKILLS.md) | Repository, runtime, dependency or build configuration | Retained |
| [alembic.ini](../alembic.ini) | Repository, runtime, dependency or build configuration | Retained |
| [artifacts/fiverr-gig-gallery/01-rag-ai-chatbot.png](../artifacts/fiverr-gig-gallery/01-rag-ai-chatbot.png) | Visual asset or historical design artifact (binary) | Retained |
| [artifacts/fiverr-gig-gallery/02-rag-okf-workflow.png](../artifacts/fiverr-gig-gallery/02-rag-okf-workflow.png) | Visual asset or historical design artifact (binary) | Retained |
| [artifacts/fiverr-gig-gallery/03-rag-capabilities.png](../artifacts/fiverr-gig-gallery/03-rag-capabilities.png) | Visual asset or historical design artifact (binary) | Retained |
| [backend/.env.example](../backend/.env.example) | Repository, runtime, dependency or build configuration | Retained |
| [backend/__init__.py](../backend/__init__.py) | Repository, runtime, dependency or build configuration | Retained |
| [backend/api/__init__.py](../backend/api/__init__.py) | HTTP API or request guard | Retained |
| [backend/api/connectors.py](../backend/api/connectors.py) | HTTP API or request guard | Retained |
| [backend/api/middleware.py](../backend/api/middleware.py) | HTTP API or request guard | Retained |
| [backend/api/routes.py](../backend/api/routes.py) | HTTP API or request guard | Retained |
| [backend/config.py](../backend/config.py) | Repository, runtime, dependency or build configuration | Retained |
| [backend/connectors/__init__.py](../backend/connectors/__init__.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/base.py](../backend/connectors/base.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/config.py](../backend/connectors/config.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/core.py](../backend/connectors/core.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/drive.py](../backend/connectors/drive.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/events.py](../backend/connectors/events.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/github.py](../backend/connectors/github.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/gmail.py](../backend/connectors/gmail.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/http.py](../backend/connectors/http.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/rates.py](../backend/connectors/rates.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/registry.py](../backend/connectors/registry.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/slack.py](../backend/connectors/slack.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/subscriptions.py](../backend/connectors/subscriptions.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/connectors/vault.py](../backend/connectors/vault.py) | Native provider transport, authorization or envelope contract | Retained |
| [backend/db/__init__.py](../backend/db/__init__.py) | Repository, runtime, dependency or build configuration | Retained |
| [backend/db/migrations/env.py](../backend/db/migrations/env.py) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/migrations/script.py.mako](../backend/db/migrations/script.py.mako) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/migrations/versions/0001_persistent_store.py](../backend/db/migrations/versions/0001_persistent_store.py) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/migrations/versions/0002_runtime_pipeline.py](../backend/db/migrations/versions/0002_runtime_pipeline.py) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/migrations/versions/0003_canonical_pipeline.py](../backend/db/migrations/versions/0003_canonical_pipeline.py) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/migrations/versions/0004_connector_layer.py](../backend/db/migrations/versions/0004_connector_layer.py) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/migrations/versions/0005_chunk_search_context.py](../backend/db/migrations/versions/0005_chunk_search_context.py) | Reviewed historical schema migration / Alembic tooling | Retained |
| [backend/db/models.py](../backend/db/models.py) | Repository, runtime, dependency or build configuration | Retained |
| [backend/db/session.py](../backend/db/session.py) | Repository, runtime, dependency or build configuration | Retained |
| [backend/main.py](../backend/main.py) | Repository, runtime, dependency or build configuration | Retained |
| [backend/processing/__init__.py](../backend/processing/__init__.py) | Parsing or former generated representation stage | Retained |
| `backend/processing/chunking.py` | Parsing or former generated representation stage | Removed |
| `backend/processing/loaders.py` | Parsing or former generated representation stage | Removed |
| [backend/processing/okf.py](../backend/processing/okf.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/__init__.py](../backend/processing/pipeline/__init__.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/canonical.py](../backend/processing/pipeline/canonical.py) | Parsing or former generated representation stage | Retained |
| `backend/processing/pipeline/chunks.py` | Parsing or former generated representation stage | Removed |
| `backend/processing/pipeline/concepts.py` | Parsing or former generated representation stage | Removed |
| [backend/processing/pipeline/config.py](../backend/processing/pipeline/config.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/context.py](../backend/processing/pipeline/context.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/execution.py](../backend/processing/pipeline/execution.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/inspection.py](../backend/processing/pipeline/inspection.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/parsers.py](../backend/processing/pipeline/parsers.py) | Parsing or former generated representation stage | Retained |
| [backend/processing/pipeline/runner.py](../backend/processing/pipeline/runner.py) | Parsing or former generated representation stage | Retained |
| `backend/processing/pipeline/splitting.py` | Parsing or former generated representation stage | Removed |
| `backend/processing/pipeline/units.py` | Parsing or former generated representation stage | Removed |
| [backend/processing/pipeline/validation.py](../backend/processing/pipeline/validation.py) | Parsing or former generated representation stage | Retained |
| [backend/repositories/__init__.py](../backend/repositories/__init__.py) | Database read/write boundary | Retained |
| `backend/repositories/corpus.py` | Database read/write boundary | Removed |
| [backend/repositories/pipeline.py](../backend/repositories/pipeline.py) | Database read/write boundary | Retained |
| `backend/repositories/prepared.py` | Database read/write boundary | Removed |
| `backend/repositories/retrieval.py` | Database read/write boundary | Removed |
| [backend/repositories/source_access.py](../backend/repositories/source_access.py) | Database read/write boundary | Retained |
| [backend/repositories/sources.py](../backend/repositories/sources.py) | Database read/write boundary | Retained |
| `backend/repositories/vectors.py` | Database read/write boundary | Removed |
| [backend/requirements.txt](../backend/requirements.txt) | Repository, runtime, dependency or build configuration | Retained |
| [backend/schemas/__init__.py](../backend/schemas/__init__.py) | API/persistence/parser contracts | Retained |
| [backend/schemas/connectors.py](../backend/schemas/connectors.py) | API/persistence/parser contracts | Retained |
| [backend/schemas/models.py](../backend/schemas/models.py) | API/persistence/parser contracts | Retained |
| [backend/schemas/pipeline.py](../backend/schemas/pipeline.py) | API/persistence/parser contracts | Retained |
| [backend/services/__init__.py](../backend/services/__init__.py) | Application orchestration or former inference service | Retained |
| [backend/services/access.py](../backend/services/access.py) | Application orchestration or former inference service | Retained |
| [backend/services/connector_sources.py](../backend/services/connector_sources.py) | Application orchestration or former inference service | Retained |
| [backend/services/connector_sync.py](../backend/services/connector_sync.py) | Application orchestration or former inference service | Retained |
| [backend/services/connectors.py](../backend/services/connectors.py) | Application orchestration or former inference service | Retained |
| `backend/services/diagnosis.py` | Application orchestration or former inference service | Removed |
| `backend/services/embedding_profiles.py` | Application orchestration or former inference service | Removed |
| `backend/services/embeddings.py` | Application orchestration or former inference service | Removed |
| `backend/services/generation.py` | Application orchestration or former inference service | Removed |
| [backend/services/ingestion.py](../backend/services/ingestion.py) | Application orchestration or former inference service | Retained |
| `backend/services/ollama.py` | Application orchestration or former inference service | Removed |
| [backend/services/readiness.py](../backend/services/readiness.py) | Application orchestration or former inference service | Retained |
| `backend/services/retrieval.py` | Application orchestration or former inference service | Removed |
| `backend/services/retrieval_config.py` | Application orchestration or former inference service | Removed |
| [backend/services/store.py](../backend/services/store.py) | Application orchestration or former inference service | Retained |
| [backend/services/uploads.py](../backend/services/uploads.py) | Application orchestration or former inference service | Retained |
| [backend/tests/conftest.py](../backend/tests/conftest.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/difficult_corpus.py](../backend/tests/difficult_corpus.py) | Regression test / isolated harness / test fixture | Retained |
| `backend/tests/fixtures/embeddings/benchmark.json` | Removed model benchmark corpus | Removed |
| [backend/tests/fixtures/pipeline/columns.pdf](../backend/tests/fixtures/pipeline/columns.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/data.csv](../backend/tests/fixtures/pipeline/data.csv) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/data.json](../backend/tests/fixtures/pipeline/data.json) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/data.xml](../backend/tests/fixtures/pipeline/data.xml) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/data.yaml](../backend/tests/fixtures/pipeline/data.yaml) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/difficult-layout.pdf](../backend/tests/fixtures/pipeline/difficult-layout.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/digital.pdf](../backend/tests/fixtures/pipeline/digital.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/events.log](../backend/tests/fixtures/pipeline/events.log) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/guide.docx](../backend/tests/fixtures/pipeline/guide.docx) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/guide.md](../backend/tests/fixtures/pipeline/guide.md) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/integrity-audit.md](../backend/tests/fixtures/pipeline/integrity-audit.md) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/integrity-audit.pdf](../backend/tests/fixtures/pipeline/integrity-audit.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/message.eml](../backend/tests/fixtures/pipeline/message.eml) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/page.html](../backend/tests/fixtures/pipeline/page.html) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/rotated-layout.pdf](../backend/tests/fixtures/pipeline/rotated-layout.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/sample.py](../backend/tests/fixtures/pipeline/sample.py) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/scan.png](../backend/tests/fixtures/pipeline/scan.png) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/scanned.pdf](../backend/tests/fixtures/pipeline/scanned.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/slides.pptx](../backend/tests/fixtures/pipeline/slides.pptx) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/speech.vtt](../backend/tests/fixtures/pipeline/speech.vtt) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/table.md](../backend/tests/fixtures/pipeline/table.md) | Input regression fixture | Retained |
| [backend/tests/fixtures/pipeline/table.pdf](../backend/tests/fixtures/pipeline/table.pdf) | Input regression fixture (binary) | Retained |
| [backend/tests/fixtures/pipeline/workbook.xlsx](../backend/tests/fixtures/pipeline/workbook.xlsx) | Input regression fixture (binary) | Retained |
| [backend/tests/test_api.py](../backend/tests/test_api.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_connector_adapters.py](../backend/tests/test_connector_adapters.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_connector_workflows.py](../backend/tests/test_connector_workflows.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_connectors.py](../backend/tests/test_connectors.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_database.py](../backend/tests/test_database.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_difficult_content.py](../backend/tests/test_difficult_content.py) | Regression test / isolated harness / test fixture | Retained |
| `backend/tests/test_generation.py` | Regression test / isolated harness / test fixture | Removed |
| [backend/tests/test_okf.py](../backend/tests/test_okf.py) | Regression test / isolated harness / test fixture | Retained |
| `backend/tests/test_ollama_embeddings.py` | Regression test / isolated harness / test fixture | Removed |
| [backend/tests/test_pipeline.py](../backend/tests/test_pipeline.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_pipeline_database.py](../backend/tests/test_pipeline_database.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_pipeline_integrity_audit.py](../backend/tests/test_pipeline_integrity_audit.py) | Regression test / isolated harness / test fixture | Retained |
| `backend/tests/test_preembedding_handoff.py` | Regression test / isolated harness / test fixture | Removed |
| `backend/tests/test_prepared_pipeline.py` | Regression test / isolated harness / test fixture | Removed |
| [backend/tests/test_runtime_pipeline.py](../backend/tests/test_runtime_pipeline.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_search_migration.py](../backend/tests/test_search_migration.py) | Regression test / isolated harness / test fixture | Retained |
| [backend/tests/test_security.py](../backend/tests/test_security.py) | Regression test / isolated harness / test fixture | Retained |
| `backend/tests/test_semantic_retrieval.py` | Regression test / isolated harness / test fixture | Removed |
| `compose.ollama-host.yaml` | Repository, runtime, dependency or build configuration | Removed |
| `compose.ollama.yaml` | Repository, runtime, dependency or build configuration | Removed |
| [compose.yaml](../compose.yaml) | Repository, runtime, dependency or build configuration | Retained |
| `configs/answers.env.example` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `configs/embedding-winner.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `configs/ollama-host-relay.conf` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `configs/ollama-host.env.example` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `configs/ollama.env.example` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/answer-results/local-smoke.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| [docs/connectors.md](../docs/connectors.md) | Architecture/operator documentation, evidence or runtime configuration | Retained |
| [docs/difficult-content.md](../docs/difficult-content.md) | Architecture/operator documentation, evidence or runtime configuration | Retained |
| `docs/embedding-results/embeddinggemma-300m.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/embedding-results/keyword.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/embedding-results/nomic-embed-text-v1.5.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/embedding-results/persistence.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/embedding-results/qwen3-embedding-0.6b.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/embedding-results/validation.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/embedding-results/winner.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| [docs/ingestion-pipeline.md](../docs/ingestion-pipeline.md) | Architecture/operator documentation, evidence or runtime configuration | Retained |
| [docs/launch-readiness.md](../docs/launch-readiness.md) | Architecture/operator documentation, evidence or runtime configuration | Retained |
| `docs/local-embeddings.md` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/preembedding-validation.md` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/analysis.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/api-smoke.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/decision.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/dense.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/hybrid.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/keyword.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| `docs/retrieval-results/validation.json` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| [docs/runtime-readiness.md](../docs/runtime-readiness.md) | Architecture/operator documentation, evidence or runtime configuration | Retained |
| `docs/semantic-retrieval.md` | Architecture/operator documentation, evidence or runtime configuration | Removed |
| [eslint.config.mjs](../eslint.config.mjs) | Repository, runtime, dependency or build configuration | Retained |
| [next.config.ts](../next.config.ts) | Repository, runtime, dependency or build configuration | Retained |
| [package-lock.json](../package-lock.json) | Repository, runtime, dependency or build configuration | Retained |
| [package.json](../package.json) | Repository, runtime, dependency or build configuration | Retained |
| [postcss.config.mjs](../postcss.config.mjs) | Repository, runtime, dependency or build configuration | Retained |
| [public/images/README.md](../public/images/README.md) | Visual asset or historical design artifact | Retained |
| [public/images/developer-workspace.webp](../public/images/developer-workspace.webp) | Visual asset or historical design artifact (binary) | Retained |
| [public/images/documentation-desk.webp](../public/images/documentation-desk.webp) | Visual asset or historical design artifact (binary) | Retained |
| [pyproject.toml](../pyproject.toml) | Repository, runtime, dependency or build configuration | Retained |
| [requirements-dev.lock.txt](../requirements-dev.lock.txt) | Repository, runtime, dependency or build configuration | Retained |
| [requirements-dev.txt](../requirements-dev.txt) | Repository, runtime, dependency or build configuration | Retained |
| [requirements-ingestion.txt](../requirements-ingestion.txt) | Repository, runtime, dependency or build configuration | Retained |
| [scripts/__init__.py](../scripts/__init__.py) | Local CLI, startup or benchmark tooling | Retained |
| [scripts/assign_legacy_owner.py](../scripts/assign_legacy_owner.py) | Local CLI, startup or benchmark tooling | Retained |
| `scripts/benchmark_difficult_content.py` | Local CLI, startup or benchmark tooling | Removed |
| `scripts/benchmark_embeddings.py` | Local CLI, startup or benchmark tooling | Removed |
| `scripts/benchmark_retrieval.py` | Local CLI, startup or benchmark tooling | Removed |
| [scripts/check_launch.py](../scripts/check_launch.py) | Local CLI, startup or benchmark tooling | Retained |
| `scripts/import_jsonl_to_db.py` | Local CLI, startup or benchmark tooling | Removed |
| [scripts/local_postgres.sh](../scripts/local_postgres.sh) | Local CLI, startup or benchmark tooling | Retained |
| `scripts/retrieval_report.py` | Local CLI, startup or benchmark tooling | Removed |
| [scripts/run_pipeline.py](../scripts/run_pipeline.py) | Local CLI, startup or benchmark tooling | Retained |
| [scripts/start-local.mjs](../scripts/start-local.mjs) | Local CLI, startup or benchmark tooling | Retained |
| [scripts/start-production.mjs](../scripts/start-production.mjs) | Local CLI, startup or benchmark tooling | Retained |
| `scripts/tests/test_embedding_benchmark.py` | Regression test / isolated harness / test fixture | Removed |
| `scripts/tests/test_retrieval_benchmark.py` | Regression test / isolated harness / test fixture | Removed |
| [scripts/tests/test_run_pipeline.py](../scripts/tests/test_run_pipeline.py) | Regression test / isolated harness / test fixture | Retained |
| [sonar-project.properties](../sonar-project.properties) | Repository, runtime, dependency or build configuration | Retained |
| [src/app/api/backend/[...path]/route.ts](../src/app/api/backend/[...path]/route.ts) | Next server gateway / callback / event ingress | Retained |
| [src/app/api/connectors/[provider]/callback/route.ts](../src/app/api/connectors/[provider]/callback/route.ts) | Next server gateway / callback / event ingress | Retained |
| [src/app/api/connectors/events/[provider]/route.ts](../src/app/api/connectors/events/[provider]/route.ts) | Next server gateway / callback / event ingress | Retained |
| [src/app/connectors/page.tsx](../src/app/connectors/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/favicon.ico](../src/app/favicon.ico) | Frontend page/provider/style/account route (binary) | Retained |
| [src/app/globals.css](../src/app/globals.css) | Frontend page/provider/style/account route | Retained |
| [src/app/history/page.tsx](../src/app/history/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/layout.tsx](../src/app/layout.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/page.tsx](../src/app/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/saved/page.tsx](../src/app/saved/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/settings/page.tsx](../src/app/settings/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/sign-in/[[...sign-in]]/page.tsx](../src/app/sign-in/[[...sign-in]]/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/sign-up/[[...sign-up]]/page.tsx](../src/app/sign-up/[[...sign-up]]/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/app/sources/page.tsx](../src/app/sources/page.tsx) | Frontend page/provider/style/account route | Retained |
| [src/components/connectors/account-panel.tsx](../src/components/connectors/account-panel.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/connectors/resource-selection.tsx](../src/components/connectors/resource-selection.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/debug/debug-input.tsx](../src/components/debug/debug-input.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/debug/diagnosis-result.tsx](../src/components/debug/diagnosis-result.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/debug/followup-chat.tsx](../src/components/debug/followup-chat.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/debug/grounded-answer.tsx](../src/components/debug/grounded-answer.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| `src/components/debug/pipeline.tsx` | Shared frontend layout, control or workflow renderer | Removed |
| [src/components/debug/rag-transparency.tsx](../src/components/debug/rag-transparency.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/debug/workspace-readiness.tsx](../src/components/debug/workspace-readiness.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/account-shell.tsx](../src/components/layout/account-shell.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/app-shell.tsx](../src/components/layout/app-shell.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/brand.tsx](../src/components/layout/brand.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/page-heading.tsx](../src/components/layout/page-heading.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/right-panel.tsx](../src/components/layout/right-panel.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/sidebar.tsx](../src/components/layout/sidebar.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/theme-provider.tsx](../src/components/layout/theme-provider.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/topbar.tsx](../src/components/layout/topbar.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/layout/workspace-intro.tsx](../src/components/layout/workspace-intro.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/ui/badge.tsx](../src/components/ui/badge.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/ui/button.tsx](../src/components/ui/button.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/ui/code-block.tsx](../src/components/ui/code-block.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/ui/highlight.tsx](../src/components/ui/highlight.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/ui/tabs.tsx](../src/components/ui/tabs.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/components/ui/toast.tsx](../src/components/ui/toast.tsx) | Shared frontend layout, control or workflow renderer | Retained |
| [src/lib/api.ts](../src/lib/api.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/chat-suggestions.ts](../src/lib/chat-suggestions.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/connector-contracts.ts](../src/lib/connector-contracts.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/connectors.ts](../src/lib/connectors.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/diagnosis.ts](../src/lib/diagnosis.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/files.ts](../src/lib/files.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/request-timeout.ts](../src/lib/request-timeout.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/response-validation.ts](../src/lib/response-validation.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/server/application-origin.ts](../src/lib/server/application-origin.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/server/backend-proxy.ts](../src/lib/server/backend-proxy.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/server/connector-events.ts](../src/lib/server/connector-events.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/sources.ts](../src/lib/sources.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/types.ts](../src/lib/types.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/use-resource.ts](../src/lib/use-resource.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/lib/utils.ts](../src/lib/utils.ts) | Typed frontend domain, validation, HTTP or shared helper | Retained |
| [src/proxy.ts](../src/proxy.ts) | Repository, runtime, dependency or build configuration | Retained |
| [tests/browser/clerk-client.tsx](../tests/browser/clerk-client.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/clerk-server.mjs](../tests/browser/clerk-server.mjs) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/clerk.tsx](../tests/browser/clerk.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/image.tsx](../tests/browser/image.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/index.html](../tests/browser/index.html) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/link.tsx](../tests/browser/link.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/main.tsx](../tests/browser/main.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/navigation.ts](../tests/browser/navigation.ts) | Regression test / isolated harness / test fixture | Retained |
| `tests/browser/preembedding.py` | Regression test / isolated harness / test fixture | Removed |
| `tests/browser/run-answers.mjs` | Regression test / isolated harness / test fixture | Removed |
| `tests/browser/run-preembedding.mjs` | Regression test / isolated harness / test fixture | Removed |
| [tests/browser/verify-connectors.mjs](../tests/browser/verify-connectors.mjs) | Regression test / isolated harness / test fixture | Retained |
| `tests/browser/verify-pipeline-audit.mjs` | Regression test / isolated harness / test fixture | Removed |
| [tests/browser/verify.mjs](../tests/browser/verify.mjs) | Regression test / isolated harness / test fixture | Retained |
| [tests/browser/vite.config.mts](../tests/browser/vite.config.mts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/answers.test.tsx](../tests/frontend/answers.test.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/api.test.ts](../tests/frontend/api.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/backend-gateway.test.ts](../tests/frontend/backend-gateway.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/components.test.tsx](../tests/frontend/components.test.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/connector-gateway.test.ts](../tests/frontend/connector-gateway.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/connectors.test.tsx](../tests/frontend/connectors.test.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/diagnosis.test.ts](../tests/frontend/diagnosis.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/files.test.ts](../tests/frontend/files.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/fixtures/diagnosis.ts](../tests/frontend/fixtures/diagnosis.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/highlight.test.tsx](../tests/frontend/highlight.test.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/layout.test.tsx](../tests/frontend/layout.test.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/pages.test.tsx](../tests/frontend/pages.test.tsx) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/proxy.test.ts](../tests/frontend/proxy.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/response-validation.test.ts](../tests/frontend/response-validation.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/tooling.test.ts](../tests/frontend/tooling.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tests/frontend/utils.test.ts](../tests/frontend/utils.test.ts) | Regression test / isolated harness / test fixture | Retained |
| [tsconfig.json](../tsconfig.json) | Repository, runtime, dependency or build configuration | Retained |
| [vitest.config.mts](../vitest.config.mts) | Repository, runtime, dependency or build configuration | Retained |

New reset files:

- `backend/db/migrations/versions/0006_parsing_only.py`: additive parsed status.
- `backend/processing/pipeline/structure.py`: parser-required syntax inspection helpers.
- `backend/repositories/parsed.py`: SQL-authorized canonical handoff.
- `backend/tests/{history,legacy_records,test_parsing_boundary}.py`: historical row fixtures and parsing-boundary regressions.
- `docs/{product-reset,project-file-inventory}.md`: this audit and inventory.
