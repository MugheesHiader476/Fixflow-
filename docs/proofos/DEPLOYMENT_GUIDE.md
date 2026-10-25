# Deployment guide

Use the existing native/Compose deployment and preserve all volumes. Do not rename PostgreSQL roles, historical gateway settings, callbacks or volume identifiers for branding. Review and back up before applying new additive migrations to an application database. This assignment validates migrations only against disposable `_test` databases.

Existing backend-only `DATABASE_URL`, `FIXFLOW_API_TOKEN`, `CONNECTORS__PUBLIC_URL`, vault/provider settings, Clerk configuration and matching gateway settings remain required. `GROK__API_KEY`/`GROK__MODEL` enable chat; Gemini remains optional embeddings. No new live secret is needed for deterministic acceptance fixtures.

Provider read access and selection remain documented in docs/connectors.md. New GitHub CI read capabilities require actual read-only App permissions and explicit selection; old installations are not silently upgraded. Slack posting and Drive uploading remain unavailable until separately implemented/consented. Packages must remain private and authenticated.

Remote repository rename is an administrator operation and is not performed. Existing remotes/deployment URLs stay compatible. Production rollout requires final executed test records, schema comparison, backups/restore, configured customer approval identities, TLS, provider consent and operational readiness; no local test establishes these live outcomes.

## Domain storage and startup

Review 0011, back up PostgreSQL and apply `myenev/bin/alembic upgrade head` through the operator release process. Eleven new `proof_*` tables/indexes preserve old records. Populated downgrade refuses deletion. Validate first on isolated _test data; fixtures truncate that database. No automatic migration/backfill occurs at startup. Reprovision existing restricted runtime grants after migration using the established provisioning command.

FastAPI starts existing workers plus the bounded ProofOS queue. No new service/vector database/framework/remote execution host is needed. JSONB stores package snapshots; existing pypdf renders downloads in a worker/thread. JSON hashes bind approval. Downloads use Clerk gateway/no-store/nosniff/attachment headers.

## GitHub permissions

Add only needed read permissions to the existing App: Actions, Checks, Deployments, Metadata and Contents for repository/branch identities. Obtain actual installation approval; settings cannot upgrade old grants. Explicitly select evidence categories and repository/branch in Connected Apps. Existing defaults/selections are unchanged. Freeze the exact 40-character SHA, expected workflow/suite/cases and deployment environment. Missing grants/reports remain unavailable/unverified.

Primary contracts: [runs](https://docs.github.com/en/rest/actions/workflow-runs), [jobs](https://docs.github.com/en/rest/actions/workflow-jobs), [artifacts](https://docs.github.com/en/rest/actions/artifacts), [checks](https://docs.github.com/en/rest/checks/runs), [deployments](https://docs.github.com/en/rest/deployments/deployments), [statuses](https://docs.github.com/en/rest/deployments/statuses). Existing signed events trigger authoritative reconciliation, never a verdict.

## Slack, Drive and customers

Keep existing read scopes/selections from docs/connectors.md; synchronize/read authorized resources and attach canonical evidence. Neither connection authorizes posting/uploading/public sharing. These writes are NOT_IMPLEMENTED, and broader consent alone cannot enable them.

Configure the customer's actual, separate Clerk subject. That user signs in to /approvals. Owner internal review binds the exact package; an explicit UI action creates the bounded request. Share the app link only through a separately authorized channel; the app sends no invitation/invoice. Email/model names/browser identities cannot replace authenticated authority.

## Release blockers

Resolve inherited dependency advisories; run complete CI with disposable PostgreSQL; verify production TLS/auth/grants/artifact reachability; test backup/restore/retention and purged-project operations; budget native rates/DB capacity; review privacy/contractual authority. Optional roles/writes/coding remain future work. Do not deploy this branch as a completed all-features production release.
