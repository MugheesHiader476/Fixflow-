# Current capability boundary

The product is being reset before selecting a new objective. This checkout is a private ingestion and parsing foundation, not a question-answering or diagnosis product.

| Capability | Current behavior |
| --- | --- |
| Accounts/gateway | Clerk and trusted private FastAPI owner isolation retained |
| Uploads/paste | Supported private content accepted with 202, then parsed asynchronously |
| Connectors | Gmail, Drive, GitHub and Slack native read-only selection/sync/permission lifecycle retained |
| Parsing | Inspection, native/layout/optional OCR, quality gates, canonical structure/provenance retained |
| Persistence | Canonical JSONB artifact plus whole-document projection; PostgreSQL authoritative |
| History/saved | Existing conversations, exact citation snapshots and exports remain readable |
| Post-parsing stages | Generated OKF/concepts, chunks, embeddings, keyword/dense retrieval and AI answers removed |
| Local models | FixFlow's downloaded EmbeddingGemma and Qwen model weights removed; shared Ollama binary retained |
| Frontend | Existing shell/layout retained; parsed status, accurate notices and disabled removed actions |

The next product decision needs a specific user, job to be done, required inputs/output, success measure, and a representative validation set. No replacement AI workflow has been assumed or implemented.

Current validation is recorded in [the reset audit](product-reset.md). Historical pre-embedding/retrieval/answer evaluations no longer describe executable features. Live Clerk/provider consent, actual deployment, OCR availability, backup restoration and capacity require their own execution evidence.
