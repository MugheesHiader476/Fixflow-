# Connected Apps: implementation, transport decisions and setup

Reviewed against current official documentation on 2026-10-03. Gmail, Google Drive, GitHub and Slack use direct native APIs. No Google Cloud Integration Connectors connection is provisioned or used. Automated tests mock external HTTP; live provider authorization and delivery require the setup below.

## Architecture and boundaries

```text
Clerk-authenticated Connected Apps UI
→ same-origin Next server gateway / OAuth callback
→ owner-scoped FastAPI connector API
→ encrypted Auth Broker / TokenVault / ConnectorAccount
→ direct provider adapter / durable PostgreSQL sync jobs
→ RawSourceEnvelope / private asset reference
→ generic source registration
→ existing inspection / parsing / canonical / OKF / chunk pipeline
→ PostgreSQL artifacts, documents and chunks
→ existing keyword retrieval with access filters in SQL
```

Provider code ends at `RawSourceEnvelope`. Parsing never receives credentials and has no provider API dependency. An envelope has a stable UUID, connection/provider identity, native resource/parent/version, MIME/filename, exactly one text payload or opaque private asset reference, SHA-256, remote timestamps, whitelisted metadata, provenance and private application access policy. Binary bytes enter the existing validated private upload store.

Connections and credentials are separate from documents. Nine new tables persist accounts, encrypted credentials, one-time OAuth states, sync jobs, resource identities, notification subscriptions, replay receipts, audit events and shared rate reservations. Alembic `0004` adds those tables plus connector identity/availability fields to sources. Manual uploads retain account/content-hash deduplication through a partial unique index; connector resources deduplicate by owner/provider/external-account/resource identity, preserving distinct resources with identical bytes. A changed native version updates the same source. The legacy importer uses the manual index predicate too.

## Managed connector evaluation

The repository uses native Linux and Docker, with no existing managed connector project, region, IAM or private network deployment. Direct APIs preserve native cursors, event verification and permission evidence without an extra cloud service, connection-node cost or latency. They are usable locally, portable and testable with HTTP doubles. We implemented one transport per provider, rather than duplicate ingestion paths.

| Provider | Managed option inspected | Selected transport and reason |
| --- | --- | --- |
| Gmail | [Configuration](https://docs.cloud.google.com/integration-connectors/docs/connectors/gsc_gmail/configure): authorization code, JWT bearer or service account; sample connector version 1; read/delete; `Messages` and `Inbox` examples; 1 transaction/second/node. | Gmail REST v1 + server Google OAuth. Native history IDs, individual MIME messages/attachments and Pub/Sub wake-ups fit this ingestion path; no managed nodes are needed. Only read scope is requested. |
| Drive | [Overview](https://docs.cloud.google.com/integration-connectors/docs/connectors/gsc_google_drive/overview), [configuration](https://docs.cloud.google.com/integration-connectors/docs/connectors/gsc_google_drive/configure): connector v1 accesses Drive API v3, connector v2 accesses API v2; File, Folder, Permission and Drive entities; CRUD plus actions including copy. | Drive REST v3 + server Google OAuth. Native changes, authoritative download capabilities, shared-drive metadata and Office exports retain structure. No assumed managed transaction rate is used. |
| GitHub | [Configuration](https://docs.cloud.google.com/integration-connectors/docs/connectors/github/configure): GitHub App/OAuth choices, dynamic schemas and read/write/delete/actions; connector v1/v2; 2 transactions/second/node. | GitHub App + REST. User access and installation permissions are intersected; installation tokens explicitly restrict repository IDs and read permissions. Commit/blob identities and signed events support incremental file ingestion. |
| Slack | [Configuration](https://docs.cloud.google.com/integration-connectors/docs/connectors/slack/configure): authorization code/user token, sample v1, dynamic entities with Channels/Messages/MessageReplies examples and DownloadFile action; 1 transaction/second/node. | Slack user OAuth + Web API. Public/private channel histories and replies retain native identities; persistent method reservations account for Slack's stricter native limits. No DM or write scopes. |

Managed configuration requires a project, enabled Connectors/Secret Manager APIs, region, connection/version, service account and IAM/secret access; networking and optional logging also need review. The documented default node range is 2–50. [Setup](https://docs.cloud.google.com/integration-connectors/docs/setup-integration-connectors) and [pricing](https://cloud.google.com/application-integration/pricing) were inspected; node-duration charges are additional operational cost. Cloud IAM controls the managed connection, provider OAuth controls external data, and application ACL controls retrieval: these are different boundaries.

Managed entity schemas, version availability, pagination and action input/output should be discovered on an actual provisioned connection before implementing a managed transport. None was provisioned here. Native history/change/event semantics must not be assumed to appear automatically through a managed entity abstraction. Gmail and Drive pages currently show a mismatched “Preview — BigQuery Connector” banner; this does not establish a reliable provider-specific GA claim. GitHub/Slack configuration pages likewise are not a verified launch-stage contract. Confirm the chosen version's console/release status before a managed deployment. We make no managed throughput, pagination, webhook or lifecycle execution claim.

## Native synchronization and content

| Provider | Initial and incremental mechanism | Content retained |
| --- | --- | --- |
| Gmail | Paginated selected-label search, UTC date bounds; anchor history before enumeration; persisted history cursor and removals. Expired history resets a resumable reconciliation. | Individual EML messages with original headers, From/To/CC/BCC, subject, labels, dates and thread ID. Large MIME bodies stored behind attachment IDs are fetched independently of attachment selection. Optional attachments are separately traceable child envelopes and their parent selection is rechecked before download. |
| Drive | Selected files, breadth-first folders or shared drives; independent user/shared-drive change anchors and cursors; moved/removed items and expired cursors handled; folder changes reconcile descendants. Same file version can reuse private bytes. | Original downloadable formats; Google Docs → DOCX, Sheets → XLSX, Slides → PPTX. File ID, parents, shared drive, owners, native permission evidence, times and version. Shared-drive permission lists paginate. |
| GitHub | Authorized App installations/repositories; pinned branch head and blob identities; changed-head code inventory; issue/PR updated markers and paginated related resources; commits/releases. Truncated recursive trees resume one subtree at a time; empty repositories preserve metadata/issues/releases. Reconciliation catches missed changes. | Separate repository metadata, code/README/docs, issues/comments, PRs/reviews/review comments, commit metadata and releases. File path, branch, commit/blob SHA and language/role; PR and commit changed-file relationships paginate as separate envelopes, preserving patches within the native 3,000-file bound. |
| Slack | Selected authorized channels; anchored history timestamps, paginated histories/replies and individual event fetches; thread/event date bounds and thread inventories; scheduled full reconciliation covers older edits/deletions. | Workspace/channel/message/thread/reply identities, author IDs/names and dates; attachment/file metadata; optional supported private file downloads. No whole-workspace blob. |

The worker processes one bounded page or resource step per iteration. Checkpoints, pending references, retries and rate-limit delays commit to PostgreSQL. Transaction advisory locks prevent simultaneous processing of a job; source advisory locks coordinate parsing and updates. Fair availability ordering avoids a busy account starving other pending jobs. Defaults: 25 discovery items, 50 MiB maximum HTTP/download body, 30-second request timeout, 100,000 discovery steps, 3 durable retries, 300-second incremental polling and daily full reconciliation. Individual failures do not restart the whole sync. `Retry-After` persists as a future job availability time.

Successful sync means fetched/queued resources, not completed parsing or embeddings. The UI polls active jobs every five seconds and connected accounts every fifteen seconds, exposes a native health check, permits clearing selections, and links to Knowledge Sources for ingestion status. Unsupported content is reported as a failure/warning; it is not fabricated or silently labeled indexed. Optional embeddings remain disabled until separately configured; product retrieval is still keyword-based.

Official native behavior references: [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Gmail sync](https://developers.google.com/workspace/gmail/api/guides/sync), [Drive changes](https://developers.google.com/workspace/drive/api/guides/manage-changes), [Workspace exports](https://developers.google.com/workspace/drive/api/guides/ref-export-formats), [shared drives](https://developers.google.com/workspace/drive/api/guides/enable-shareddrives), [GitHub user tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app), [installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app), [Slack OAuth](https://docs.slack.dev/authentication/installing-with-oauth), [history](https://docs.slack.dev/reference/methods/conversations.history) and [replies](https://docs.slack.dev/reference/methods/conversations.replies).

## Authorization, events and retrieval

Next verifies Clerk and supplies the existing private gateway credential plus verified application owner. All connector lifecycle, discovery, query and status APIs filter by that owner. Browser-selected IDs are probed using native authorization before configuration or ACL renewal. OAuth state is random, owner/provider bound, hashed, expires after ten minutes and is consumed once before code exchange. Google/GitHub use PKCE; Slack uses its server authorization-code flow and state. Callback origins are fixed by server configuration, not browser return URLs. Credentials use encrypted Fernet storage with opaque references, row-locked refresh/rotation and no token fields in public responses or envelopes. Unknown JSON fields/invalid dates are rejected; non-finite provider JSON is rejected before persistence.

Provider ACL evidence propagates into canonical metadata, OKF concept context/frontmatter, chunk context and persisted artifacts. Application visibility is deliberately private to the connecting owner. Native permissions are evidence, not an automatic grant to another application user. Keyword and dormant vector queries filter ownership, active state, connection authorization and unexpired access lease in SQL before reading content. Default lease: one hour. Selected roots are rechecked during long scans and before lease renewal. A lost selected root disables the previous inventory and reconciles the remaining authorized roots with a visible configuration warning; one missing root does not block the account. Explicit disconnect/revocation disables access immediately; permission loss is otherwise bounded by event/poll/reconciliation and lease expiry. This is not instantaneous global provider ACL mirroring or a group-sharing feature.

Only the exact POST `/api/connectors/events/{gmail|google_drive|github|slack}` bypasses Clerk; the private backend receives bounded raw bytes under its separate event boundary. Events are optional and default disabled. Verification occurs before parsing/enqueueing:

- Gmail: Google RS256 OIDC signature, issuer, expiry, exact audience, configured push-service-account email and verified-email claim. JWKS cache uses a bounded TTL; untrusted key IDs do not evict it.
- Drive: random channel token hash, channel UUID, resource ID, expiry and message receipt identity.
- GitHub: SHA-256 HMAC over raw bytes. Replay identity hashes signed bytes, so an altered unsigned delivery header cannot bypass deduplication.
- Slack: v0 raw-body signature and five-minute timestamp window, plus event ID deduplication. Signed URL verification is supported.

Receipts and wake-ups commit together; events identify resources but never grant access. Workers fetch authoritative current state or native changes. Signed Slack revocation events wipe only the affected OAuth user’s credentials and cancel their jobs; GitHub installation/repository removal disables the mapped repository content and cancels stale in-flight inventories before a new reconciliation. Event references respect configured GitHub categories. Optional Gmail/Drive subscription failures show a warning while polling continues; expired channels reject delivery. Renewal runs before subscription expiry. Signatures: [GitHub](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries), [Slack](https://docs.slack.dev/authentication/verifying-requests-from-slack), [authenticated Pub/Sub push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions), [Gmail push](https://developers.google.com/workspace/gmail/api/guides/push), [Drive push](https://developers.google.com/workspace/drive/api/guides/push).

Disconnect commits local credential wiping, sync cancellation and retrieval exclusion before best-effort remote watch cleanup/revocation. `retain` keeps disabled content; `soft_delete` also marks resource tombstones; `purge` deletes sources/artifacts/chunks, resource rows, private job payloads and local files before remote cleanup, retaining connection/audit history. Failed remote cleanup is visible and directs the user to provider settings. Reconnecting clears the prior cursor, disables retained retrieval, and forces an initial inventory even when the user immediately clicks Sync Now. Disconnect does not uninstall a GitHub App or Slack workspace installation; administrators manage those provider-side. Google revocation can invalidate other grants for the same OAuth application.

## Server configuration

Set backend values in `backend/.env` for native development, or root `.env` for Compose. See the two tracked `.env.example` files; provider secrets must never be `NEXT_PUBLIC_*` or placed in the browser. Existing Clerk, gateway token, database and private data directory configuration is still required.

The Next server also needs the non-secret `CONNECTORS__PUBLIC_URL` value. Compose passes it to both services; native Next reads it from the root `.env` or `.env.local`. The gateway validates browser writes against this fixed public origin, and OAuth callbacks redirect there, so Docker's internal bind address cannot reject legitimate requests or become a redirect destination. Provider/vault credentials remain backend-only in Compose; forwarded browser host headers cannot override the configured origin.

| Purpose | Variables |
| --- | --- |
| Public Next origin / vault | `CONNECTORS__PUBLIC_URL`, `CONNECTORS__VAULT_KEY`, optional `CONNECTORS__VAULT_PREVIOUS_KEYS` JSON array |
| Google OAuth | `CONNECTORS__GOOGLE_CLIENT_ID`, `CONNECTORS__GOOGLE_CLIENT_SECRET` |
| GitHub App | `CONNECTORS__GITHUB_CLIENT_ID`, `CONNECTORS__GITHUB_CLIENT_SECRET`, `CONNECTORS__GITHUB_APP_ID`, `CONNECTORS__GITHUB_APP_SLUG`, `CONNECTORS__GITHUB_PRIVATE_KEY`, `CONNECTORS__GITHUB_WEBHOOK_SECRET`, `CONNECTORS__GITHUB_API_VERSION` |
| Slack | `CONNECTORS__SLACK_CLIENT_ID`, `CONNECTORS__SLACK_CLIENT_SECRET`, `CONNECTORS__SLACK_SIGNING_SECRET` |
| Optional events | `CONNECTORS__EVENTS_ENABLED`, `CONNECTORS__GMAIL_PUBSUB_TOPIC`, `CONNECTORS__GMAIL_PUBSUB_SERVICE_ACCOUNT` |
| Scheduling | `CONNECTORS__POLLING_SECONDS`, `CONNECTORS__RECONCILIATION_SECONDS`, `CONNECTORS__ACL_TTL_SECONDS`, `CONNECTORS__SLACK_HISTORY_INTERVAL_SECONDS` |
| Bounds | `CONNECTORS__REQUEST_TIMEOUT_SECONDS`, `CONNECTORS__MAX_RESPONSE_BYTES`, `CONNECTORS__PAGE_SIZE`, `CONNECTORS__MAX_SYNC_RESOURCES`, `CONNECTORS__MAX_RETRIES`, `CONNECTORS__USER_ACTIONS_PER_MINUTE` |

Generate a vault key once, store the result privately and preserve it across restarts/backups:

```bash
myenev/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

During rotation keep previous keys until all old credentials have been rotated, refreshed or reconnected. Losing the encryption key prevents token recovery; it does not prevent local disconnect. GitHub PEM can be quoted in the private dotenv file with escaped `\n` sequences. The default REST version is `2026-03-10`; do not assume installation tokens have a fixed 40-character length.

## Provider-side setup required

Use `CONNECTORS__PUBLIC_URL=http://localhost:3000` for local OAuth; use your actual HTTPS Next origin for deployment/events. Register these exact callback URLs:

```text
{origin}/api/connectors/gmail/callback
{origin}/api/connectors/google_drive/callback
{origin}/api/connectors/github/callback
{origin}/api/connectors/slack/callback
```

1. **Google:** create a web OAuth client, configure consent/test users, enable Gmail and Drive APIs and register both callbacks. Requested scopes are `openid`, `https://www.googleapis.com/auth/userinfo.email`, plus `gmail.readonly` or `drive.readonly` under `https://www.googleapis.com/auth/`. These read scopes are needed for existing selected mailbox/files discovery; production consent verification/restricted-scope assessment and organization policy approval may apply. No mail send/delete or Drive write scope is requested.
2. **Optional Gmail events:** create a Pub/Sub topic in the OAuth project's project, allow `gmail-api-push@system.gserviceaccount.com` to publish, create an authenticated push subscription to `{origin}/api/connectors/events/gmail`, and use that exact URL as OIDC audience. Set its service-account email in backend configuration. Configure Pub/Sub service-agent token-creation/IAM requirements from the linked push documentation. The application creates and renews mailbox watches; Pub/Sub infrastructure is operator-owned.
3. **Optional Drive events:** use reachable HTTPS `{origin}/api/connectors/events/google_drive` and enable events. The worker creates/renews/stops its own changes channels; no browser-provided channel token is trusted.
4. **GitHub:** [register a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app), configure the callback, client secret, App ID/slug/private key and webhook secret/URL `{origin}/api/connectors/events/github`. Repository permissions: Metadata read, Contents read, Issues read and Pull requests read. Install it on selected repositories. Subscribe to push, issues, issue_comment, pull_request, pull_request_review, pull_request_review_comment, release, installation, installation_repositories and github_app_authorization events supported by your App. Users authorize the App and select repositories in Connected Apps. No repository writes are requested.
5. **Slack:** create an app, register its callback, configure user scopes `channels:read`, `channels:history`, `groups:read`, `groups:history`, `users:read`, `files:read`, and install/authorize in the desired workspace. Private channels must be permitted to the authorizing user. For optional events, set `{origin}/api/connectors/events/slack`, signing secret, and user events message.channels, message.groups, channel_deleted, group_deleted, member_left_channel, file_deleted, file_unshared, tokens_revoked and app_uninstalled where permitted. User-token rotation is supported when enabled by Slack. No bot identity is inferred to authorize private channels.

Slack's non-Marketplace commercial history/replies restrictions can be one request/minute with 15 items; default durable method reservations use 60 seconds and at most 15 items. Internal/Marketplace allowances differ. Only tune the configured interval after checking your actual app's policy; [token rotation](https://docs.slack.dev/authentication/using-token-rotation) and current method docs are authoritative.

No Google managed-connector IAM/service-account/Secret Manager setup is needed for the selected native transport. Google OAuth and optional Pub/Sub setup still require their own cloud project permissions.

## APIs and controlled queries

Browser code uses `/api/backend` plus these private backend routes:

```text
GET  /api/connectors
POST /api/connectors/{provider}/connect
GET  /api/connectors/{provider}/callback?state=...&code=...
GET  /api/connectors/{account_id}/resources?cursor=...
GET  /api/connectors/{account_id}/status
POST /api/connectors/{account_id}/configure
POST /api/connectors/{account_id}/sync
POST /api/connectors/{account_id}/health
POST /api/connectors/{account_id}/query
POST /api/connectors/{account_id}/disconnect
```

`sync` accepts `{"mode":"incremental"}` or `{"mode":"reconcile"}` and returns 202. Query accepts only the typed operation (`list|count`), selected resource ID, exact email sender/recipient where supported, dates, bounded limit and opaque cursor. Gmail supports a single selected label and date/address filters; Slack supports a selected channel and dates; Drive supports selected resource discovery; GitHub supports selected repository metadata. Date queries cannot expand configured date bounds. There is no arbitrary SQL, provider URL, API method or LLM-generated provider query. `count` is the current page's count, not a mailbox-wide estimate; accumulate every returned page. `exact=true` indicates the terminal page. No full query planner or total-count UI exists.

## Implemented files

| Area | Files |
| --- | --- |
| Contracts/config/native transports | `backend/schemas/connectors.py`, `backend/connectors/{config,core,base,http,vault,registry,rates,gmail,drive,github,slack,subscriptions,events}.py`, `backend/config.py`, `backend/requirements.txt`, `requirements-dev.lock.txt` |
| API/lifecycle/durable synchronization | `backend/api/connectors.py`, `backend/services/{connectors,connector_sync,connector_sources}.py`, `backend/main.py` |
| Persistence/access/pipeline bridge | `backend/db/models.py`, `backend/db/migrations/versions/0004_connector_layer.py`, `backend/repositories/{source_access,retrieval,vectors,sources}.py`, `backend/processing/pipeline/{context,runner,execution}.py`, `backend/schemas/{pipeline,models}.py`, `backend/services/{ingestion,embeddings}.py` |
| Compatibility/configuration | `backend/api/routes.py`, `scripts/import_jsonl_to_db.py`, `.env.example`, `backend/.env.example`, `compose.yaml` |
| UI/gateway | `src/app/connectors/page.tsx`, `src/components/connectors/{account-panel,resource-selection}.tsx`, `src/app/api/connectors/[provider]/callback/route.ts`, `src/app/api/connectors/events/[provider]/route.ts`, `src/lib/{connectors,connector-contracts,api,response-validation,sources,types}.ts`, `src/lib/server/{backend-proxy,connector-events}.ts`, `src/proxy.ts`, `src/components/layout/sidebar.tsx`, `src/app/sources/page.tsx` |
| Tests | `backend/tests/{test_connectors,test_connector_adapters,test_connector_workflows,conftest,test_api,test_database}.py`, `tests/frontend/{connectors.test.tsx,connector-gateway.test.ts,proxy.test.ts}`, `tests/browser/{main.tsx,verify-connectors.mjs}` |

## Verified checks and exact commands

Connector verification on 2026-10-03 exercises all four native adapters through real OAuth broker/vault/account persistence, discovery/selection, initial and incremental jobs, reconciliation, parsing, canonical/OKF artifacts, chunks, private retrieval and purge. External provider HTTP is mocked; GitHub App JWTs are signed and verified with a disposable RSA key, and the Slack quota clock is accelerated while the real PostgreSQL rate reservations run. Regression tests also cover large/empty GitHub trees, commit-file pagination, per-drive cursors, Gmail external MIME bodies, Slack thread/event dates, selected-root access loss, token refresh/rotation, revocation and stale-job cancellation. No connector production path depends on test doubles. Migration fixtures downgrade/upgrade the disposable test database and verify a repeatable single head; these changes use the existing JSONB contracts and require no additional schema revision.

The complete validation suite passed on 2026-10-03: 206 Python tests (no skips, including disposable PostgreSQL and native OCR), 93 frontend tests, frontend/Python coverage, ESLint, TypeScript, Ruff, mypy (82 files), the Next.js production build, Alembic schema comparison, Bandit, the locked dependency audit, Compose configuration and diff checks. Seven browser checks passed for provider cards, resource selection, health, sync polling, mobile/modal behavior, disconnect and the provider redirect. Initial lint/type errors in test code were corrected and those failed gates rerun. The four provider workflow tests and targeted static checks passed again after strengthening their signing/quota verification. Pytest reports one existing LangChain legacy-loader deprecation warning. Live provider consent, real webhook delivery and live Clerk login require operator credentials and are not claimed from automated tests. Browser evidence uses real components with the gateway/provider boundary mocked.

Native start (existing private environment configured):

```bash
bash scripts/local_postgres.sh start
myenev/bin/alembic upgrade head
myenev/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# In a separate terminal:
npm run dev
```

Install updated backend dependencies with `myenev/bin/python -m pip install -r requirements-dev.txt`; for the committed CI resolution use `--require-hashes -r requirements-dev.lock.txt`. On a fresh frontend install use `npm ci`. Configured Docker alternative: `docker compose up -d` with the required private root `.env` values.

This workspace's already-created disposable test database:

```bash
export TEST_DATABASE_URL='postgresql+asyncpg:///fixflow_okf_runtime_20261002_test?host=/home/mughees-haider/RAG/.local/postgres&port=55432'
myenev/bin/python -m pytest
myenev/bin/python -m ruff check backend scripts
myenev/bin/python -m mypy backend scripts
DATABASE_URL="$TEST_DATABASE_URL" myenev/bin/alembic check
myenev/bin/python -m bandit -r backend scripts -x backend/tests,scripts/tests -q
myenev/bin/python -m pip_audit -r requirements-dev.lock.txt
npm run lint
npm run typecheck
npm test
npm run test:coverage
npm run build
docker compose config --quiet
git diff --check
```

The test database is erased/rebuilt by fixtures and must end in `_test`; use an independently created disposable database on another machine. To include real OCR in this native workspace's suite, prefix pytest with:

```bash
PATH="$PWD/.local/ocr/runtime/usr/bin:$PATH" \
LD_LIBRARY_PATH="$PWD/.local/ocr/runtime/usr/lib/x86_64-linux-gnu" \
TESSDATA_PREFIX="$PWD/.local/ocr/runtime/usr/share/tesseract-ocr/5/tessdata" \
myenev/bin/python -m pytest
```

Browser verification uses the existing Vite/Playwright harness. Install Playwright separately outside the repository, start `node_modules/.bin/vite --config tests/browser/vite.config.mts`, then set `FIXFLOW_BROWSER_TOOLS` to that installation's `playwright` package directory and run `node tests/browser/verify-connectors.mjs`. This run used the bundled Playwright package at `/home/mughees-haider/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright` and `/usr/bin/google-chrome`; these paths are local tooling, not shipped runtime dependencies.

## Remaining limits

Live credentials and provider approval/setup are required before enabling any card. Google Drive exports are subject to the native [10 MB export limit](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/export); unsupported formats/disabled downloads fail safely. Drive shortcuts are not followed. GitHub trees resume through non-recursive subtrees within the configured resource bound; a single directory exceeding the provider response limit fails safely. Commit/PR file inventories paginate up to the provider’s 3,000-file maximum; commit diffs/contents and repository history are not a full Git mirror. Slack DMs, unrestricted private channels and a total-count query planner are not implemented. Supported file parsing still depends on existing OCR/transcription/layout adapters.

Permissions and old deletions are eventually consistent within polling/reconciliation/lease bounds; there is no cross-user provider group-sharing policy. Connector action and resource limits are configurable. Deployment storage capacity, backup/secret-store policy and provider quotas are operator choices; the shipped application uses its encrypted persistent vault and existing private upload store. Account purge does not erase independent previously saved diagnosis snapshots/history. Embeddings, vector/hybrid product retrieval, reranking and diagnosis generation remain separate optional/unimplemented capabilities. No external authentication or paid model call was claimed from mocked tests.
