# Runtime readiness

FixFlow currently provides authenticated source registration, private uploads, owner-selected native connectors, durable parsing, and preserved history/saved snapshots. Processing ends at canonical output; questions, chunking, embeddings, retrieval and AI answers are removed.

`GET /health` is public and reports read-only database/schema readiness without owner counts. The authenticated `/api/readiness` includes owner-scoped stored counts and `processing_mode="parsing_only"`. Compatibility fields report `embedding_configured=false`, `ai_generation="removed"`, `answer_service="removed"`, and `retrieval_mode="removed"`. Counts for old chunks/vectors describe preserved storage, not active functionality. Pgvector remains a historical schema dependency, including on fresh databases, because migrations and existing vector rows are preserved.

The existing Next/Clerk gateway and FastAPI trusted server identity delegation remain unchanged. Browser requests use `/api/backend`; never expose the gateway secret or provider credentials. Backend/DB remain private. The fixed connector public origin must reach both servers; do not trust arbitrary forwarded host headers.

`POST /api/documents` returns 202 and is followed by the worker's `uploaded → processing → parsed` or safe `failed` state. Query/debug/chat/embedding retry endpoints retain authenticated 410 responses for stale clients and create no records. Existing session/messages/saved GET endpoints and saved snapshot POST remain owner-scoped.

Migration `0006` adds the parsed status only. Run `myenev/bin/alembic upgrade head` on the configured target before starting the changed worker. Old statuses, chunks/vectors, sessions, snapshots and migration history remain. The migration refuses downgrade while parsed rows exist rather than silently relabeling them.

`npm run dev:local` / `npm run start:local` starts the database helper when configured, checks matching gateway configuration, then starts FastAPI and Next. It never starts/downloads a model. `myenev/bin/python -m scripts.check_launch` checks configuration/schema prerequisites without inference, credentials in output, or data mutations.

Use disposable `_test` PostgreSQL databases for integration tests. Fresh migrations, parsing persistence, account boundaries and connector workflows use mocked provider HTTP. Live Clerk sign-in, provider consent/event delivery, deployed TLS/routing, backup restoration and production capacity remain operator verification tasks. Do not use old benchmark/browser reports as evidence for this reset.
