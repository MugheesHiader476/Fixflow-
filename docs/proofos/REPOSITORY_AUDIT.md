# ProofOS repository audit

Audit date: 2026-10-25. Starting revision: `965a53a`, branch `main`; working tree clean. The configured origin is `i243181-sys/Fixflow-`; the user explicitly selected `MugheesHiader476/Fixflow-` for eventual delivery. Do not change origin or push to the other repository implicitly.

## Reusable architecture

- Next 16.3.8 / React 19.2.8 App Router, Clerk, existing `AppShell`, theme variables and ff components. Installed Next page/route/client/icon documentation was inspected before frontend changes. Keep the home chat composer and old session restoration.
- `src/lib/api.ts` → strict same-origin gateway allowlist → server-owned bearer/verified Clerk subject → private FastAPI. No browser owner ID or token is authoritative. Extend route allowlists explicitly.
- Chat creates a durable `AgentRun` with no email authority. Captured selections, versioned original prompt, bounded model/tool attempts, pending calls, lease tokens, access checks and cancellation protect each step. Grok remains generation; Gemini remains optional embeddings.
- PostgreSQL SQLAlchemy `Owned`/UUID/JSONB models, transactional async sessions, migrations 0001–0010. Existing project association is a free-text passage index label; there is no customer, structured project, contractual approval or milestone model. Extend owner isolation rather than invent another tenant identity.
- Private source uploads → generated paths/50 MiB validation → durable source job → spawned bounded canonical parser → atomically validated JSONB and whole-document projection. `parsed_source` authorizes in SQL before decoding and validates registered owner/hash. Canonical sources have hashes, numeric versions, block IDs, exact text and provenance; the current artifact is replaced on reparse, so signed-off contract snapshots need independent immutable versions.
- Separate structure-aware passages and PostgreSQL lexical/optional pgvector retrieval remain available; legacy chunks/local inference remain retired.
- Durable parsing, connector, embedding and agent jobs already establish two-second polls, advisory locks, persisted availability/backoff, bounded provider operations and restart recovery. ProofOS jobs must use equivalent persisted claims and fencing, never long chat-request synchronization.
- Native connectors own all provider HTTP and credentials. GitHub intersects selected numeric repositories, live user access, App installation and downscoped token permissions. Existing categories include code/issues/PRs/commits/releases, but no trustworthy CI test evidence. Slack is read-only selected history/threads with durable rate reservations; Drive supports selected-resource ancestry/version checks and downloads. Neither posting nor package uploading currently exists.
- Existing signed event routes verify raw signatures/identity and deduplicate receipts before authoritative reconciliation. Webhooks are observations, not approvals or test verdicts.
- Disconnect disables local credentials/access and cancels jobs before remote cleanup; purge clears connector artifacts/workflow payloads while independent saved history survives. New derived evidence needs the same access discipline.

## Required extensions

Owner-scoped projects, frozen contract versions and requirements, explicit acceptance policies and milestone mappings; typed provider evidence; immutable verification executions; private versioned acceptance packages; separate internal/customer approval identities; exact-contract invoice drafts; durable ProofOS jobs; read/computational chat tools. Read-only evidence grants must not become write grants. Slack/Drive writes and a coding runner remain disabled unless their complete authorization boundaries are implemented and tested.

## Baseline and CI

Baseline: frontend lint/typecheck/build passed, 184 tests / 19 files; backend Ruff/mypy (138 files)/Bandit/schema comparison passed, 552 tests passed / two OCR skips,81% coverage. Newly created proofos_baseline_test was used, never application data. Dependency audit failed before changes: 52 vulnerabilities in four locked packages. Existing initialized local cluster was safely started. Local Python 3.14.4; CI 3.12. Final counts are in TEST_RESULTS.md.

The SonarQube workflow already executes `scripts/evaluate.py`: lint, typecheck, frontend coverage, Python static/security checks, tests with ephemeral PostgreSQL/pgvector, migration comparison, dependency audit, build, browser components and full-stack simulation. Extend this evaluator rather than replace Sonar or claim live identity/provider verification. `.local`, environments, uploaded files and generated outputs remain ignored. Existing Markdown ignore policy needs a narrow exception for these requested versioned records.
