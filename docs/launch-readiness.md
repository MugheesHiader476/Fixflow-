# FixFlow: source-to-answer readiness

The launch workflow is: sign in, upload a supported source or connect an authorized app, wait for **Ready to ask**, ask a question, inspect its quoted evidence, continue the conversation, and save or export the answer. Parsing, chunking, embedding identity and local model settings are operator concerns.

This release adds optional local answer synthesis and document-grounded reasoning to the existing private retrieval platform. The model can combine passages, apply documented rules, compare values and suggest supported next steps. It is a launch candidate, not a certification that every document is extractable or every model interpretation is correct. Public deployment requires the operator checks below.

## Audit and implemented gaps

| Area | Existing implementation | Completed in this change |
| --- | --- | --- |
| Accounts | Clerk pages, protected gateway, trusted owner delegation, PostgreSQL owner isolation | New question and source-availability endpoints use the same protection |
| Uploads | 50 MiB streamed private uploads, duplicate reuse, durable worker, safe failures and retries | Clear acceptance wording, user-facing readiness, upload/connector entry points |
| Formats | Native document, Office, structured data, code, logs, email and transcript adapters; canonical/OKF/chunk coverage validation | Existing adapters retained; generation accepts only authorized validated v2 projections |
| Connectors | Gmail, Drive, GitHub App, Slack; encrypted credentials, selected resources, durable incremental sync, expiring access leases | Existing source pipeline is shared by generated answers; connector permission boundaries are preserved |
| Retrieval | Keyword baseline, pinned EmbeddingGemma dense retrieval, keyword fallback when vectors are missing/incompatible | Actual retrieval method recorded; source identity, version/location and continuation labels reach the answer layer |
| Answers | Documentation excerpts; no generation adapter | Pinned loopback-only Ollama chat, strict structured claims, exact quote and citation checks, explicit abstention, safe errors |
| Conversations | Persistent sessions and follow-ups, history restoration | General question input; contextual retrieval from prior user questions; old assistant answers are never reused as current evidence |
| Saved findings | Independent JSONB snapshots and Markdown export | Answer text, numbered quotes, locations, source IDs and links preserved in saved/exported results |
| Source lifecycle | Connector disconnect/removal, administrative source deletion | Owners can remove manual uploads from search and explicitly restore them; connector sources remain managed by connector selections |
| Readiness | Database/schema/count checks | Private model-identity readiness and read-only local launch preflight |

## Local model setup

Use a native backend with Ollama listening only on loopback. The current answer candidate is `qwen3:4b-instruct-2507-q4_K_M`, approximately 2.5 GB. It is independent of the selected 768-dimensional EmbeddingGemma model; answer-model selection does not replace application vectors.

```bash
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=f16 OLLAMA_GO_TEMPLATE=1 ollama serve
# In another terminal:
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull embeddinggemma:300m
```

Verify both full digests using `/api/tags`. Copy the non-secret values in `configs/answers.env.example` and `configs/ollama.env.example` into your backend environment. For automatic source-to-answer operation, set `RETRIEVAL_MODE=dense` and `EMBEDDING_AUTO_PROCESS=true`. Keep the existing DB and gateway credentials; never replace environment files wholesale. Restart FastAPI after changes. Existing old sources are not automatically enrolled into embedding; reprocess/re-upload them explicitly if needed.

The answer model is pinned to `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`. The embedding pin remains in `configs/embedding-winner.json`. A changed/missing answer digest produces a safe availability error rather than silently switching models. The answer candidate is smoke-tested, not the winner of a comprehensive generation benchmark.

The implementation follows Ollama's [chat API](https://docs.ollama.com/api/chat), [JSON-schema structured outputs](https://docs.ollama.com/capabilities/structured-outputs), and [model inventory API](https://docs.ollama.com/api/tags). Model distribution details are in the [Qwen3 instruction listing](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M).

The installed Ollama 0.34.2 runtime selected a thinking-capable GGUF template for the instruction variant unless `OLLAMA_GO_TEMPLATE=1` was explicit. The local launcher forces the model's Ollama template, and the answer prompt states the grounding rules concisely. The previous thinking model and longer prompt produced malformed or incomplete output on the repository-code question; strict validation rejected it. Do not strip reasoning markers or accept partial JSON to conceal these failures. A fresh correction remains bounded to one attempt within the existing deadline. Restart existing answer runtimes with the explicit template setting. The earlier release-level browser results below describe the previous candidate and do not certify the replacement across all customer questions.

The current native launcher uses separate runtimes: EmbeddingGemma on CPU with f16 and the instruction answer candidate on the GPU with q8_0. It verifies the pinned model before and after a small structured-output warm-up, then starts the application. Thirteen live checks passed after a cold restart against a separate disposable `_test` database: the actual `src/lib/request-timeout.ts` file was parsed, embedded and answered with the correct 120,000/60,000 millisecond values and exact quotes; conversations/saved quotes persisted; a two-row CSV calculation returned a difference of 300 and a 33.33% increase without invented currency; missing evidence abstained; foreign-account reads were excluded. The ignored `.local/runtime/code-smoke.json` contains this narrow evidence. This is not a comprehensive model/cache quality benchmark or live Clerk verification. The older Docker/cache measurements below remain historical deployment evidence.

```bash
myenev/bin/python -m scripts.check_launch
```

This read-only command checks the database/schema, gateway credential presence, local model identities, dense retrieval configuration, automatic embedding configuration and configured public origin. It prints no credentials, performs no generation, and changes no data. Its successful result covers local prerequisites only.

For native production review, build the frontend and start the two application processes in separate terminals:

```bash
npm run build
myenev/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
# In another terminal:
npm run start -- --hostname localhost --port 3000
```

Open `http://localhost:3000`, matching the configured native public origin. The production starter loads the existing native environment, copies public/static assets, and runs Next's standalone server. It defaults to `localhost` for the native Clerk rewrite path; explicitly review the bind/public-origin settings for another deployment. Keep `INTERNAL_API_URL=http://127.0.0.1:8000` and the matching server-only gateway token in the frontend environment. The Docker image continues using its existing standalone entrypoint.

## Optional Docker local AI

`compose.ollama.yaml` adds an Ollama sidecar sharing the backend's network namespace. Both local transports can use loopback without exposing Ollama to browser clients or publishing port 11434. Set a reviewed `OLLAMA_IMAGE` tag/digest in root `.env`, along with the same non-secret model pins and automatic embedding configuration.

```bash
docker compose -f compose.yaml -f compose.ollama.yaml up -d
docker compose -f compose.yaml -f compose.ollama.yaml exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker compose -f compose.yaml -f compose.ollama.yaml exec ollama ollama pull embeddinggemma:300m
docker compose -f compose.yaml -f compose.ollama.yaml exec backend python -m scripts.check_launch
```

To make subsequent plain `docker compose up` commands include AI, set `COMPOSE_FILE=compose.yaml:compose.ollama.yaml` in root `.env`. Native `backend/.env` model settings do not configure Docker: Compose reads the root environment and passes settings explicitly. Keep both generation and dense/automatic embedding settings there.

The overlay has no GPU reservation by default. Provision GPU access deliberately or measure CPU latency before serving users. Small Docker VM memory limits can kill a 4B model with its context cache. The answer adapter uses an inference batch of 128 to reduce memory pressure; the overlay supports `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE` (default `f16`). Validate quality and latency on the actual machine; see Ollama's [cache memory guidance](https://docs.ollama.com/faq#how-can-i-set-the-quantization-type-for-the-kv-cache). Never point a container's own loopback at a host Ollama process without this shared namespace or an equivalent reviewed design.

### Docker Desktop with native host Ollama

On this workstation, Docker Desktop has a 3.5 GiB VM and no GPU runtime. The CPU sidecar exceeded memory/startup budgets. The active configuration uses `compose.ollama-host.yaml` instead: a small Nginx relay shares the backend's network namespace and listens only on its loopback ports. Port 11434 forwards only `GET /api/tags` and `POST /api/embed` to the fixed Docker Desktop host service on 11434; port 11435 forwards only `GET /api/tags` and `POST /api/chat` to the native host service on 11435. No port is published, incoming headers/credentials are discarded, model-management routes and URL query parameters are rejected, bodies are bounded, and model digest/output checks remain in the backend. Source data stays on this machine.

Keep both native Ollama runtimes running on host loopback with the installed pins. `configs/ollama-host.env.example` records the active Docker settings: embedding URL on 11434, generation URL on 11435 and a 20-second query-embedding bound to accommodate cold startup. Separate runtimes avoid unloading the answer model for every retrieval query. On this small workstation, embedding runs on CPU with the original `f16` setup, leaving GPU memory for generation. The answer runtime uses a `q4_0` attention cache and an 8192-token context. Cache quantization can affect answer quality; the recorded reasoning checks are smoke tests, not a comprehensive precision comparison. A larger production GPU can use a higher-precision cache after evaluation.

```bash
# Embedding runtime on CPU (first terminal):
CUDA_VISIBLE_DEVICES=-1 OLLAMA_VULKAN=0 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=f16 ollama serve
# Answer runtime (second terminal):
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q4_0 OLLAMA_GO_TEMPLATE=1 ollama serve
```

Copy the host overlay settings into root `.env` while retaining credentials and model pins. Recreate the relay whenever the backend's container/network namespace is recreated (normal Compose startup does this). This host design was verified with Docker Desktop; do not assume an ordinary Linux Engine's host-gateway reaches a loopback-only host service. The container Ollama overlay remains an alternative for provisioned deployment hardware.

The transport follows [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/) and [Nginx's fixed upstream proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass). Host model availability and startup at login/boot are operator responsibilities; the authenticated readiness check reports unavailable models safely.

## Answer behavior and limits

- Answers use the documents as context and synthesize a response to the question. The prompt permits comparisons, rule application, calculations and recommendations supported by the available passages. Derived answers are labelled **Conclusion** and suggested actions **Suggested next step**. User-provided scenarios remain conditional assumptions; missing information and disagreements must be made explicit.
- Model questions retrieve up to 12 ranked passages, add immediate neighboring context from independently validated sources (24 passages maximum), and pack different sources into the bounded prompt. This helps retain conditions, exceptions and adjacent data without changing the measured keyword/dense ranking or embedding model. It remains bounded context, not a guarantee that every relevant passage or complete dataset is supplied.
- The model sees short per-request citation aliases (E1, E2, etc.) and the same response schema used by the constrained decoder. It selects evidence IDs without generating quote text. The server accepts only those aliases, resolves them to original authorized source/chunk IDs, and attaches each selected passage verbatim, preserving whitespace. Quotes can include the complete bounded passage, including related conditions and rows. No public or saved citation identity changes.
- Every returned fact or conclusion has numbered evidence references. IDs are constrained to the supplied set in the model schema and checked again by the server; unexpected model quote fields are rejected. The UI renders model text as text; it never executes source content or model tools. The API exposes the concise answer and cited basis, not the model's private reasoning trace.
- A conservative server check rejects added currency symbols and common currency names/codes absent from the quoted premises. Invalid structured output, reference selection or units permit at most one fresh correction attempt within the original model deadline; the invalid draft is never saved, echoed to the model or silently edited. This check does not validate every unit, numeric claim or semantic relationship.
- Prompt instructions classify source content and prior questions as untrusted data. This reduces instruction-following risk but does not prove resistance to every prompt injection.
- Missing evidence returns an explicit abstention without calling the model when no validated evidence is available. Provider errors, malformed output, fabricated quotes, changed digests, truncated output and source changes during generation return safe retryable errors; an invalid answer is not persisted.
- Source access, status, hash and independently validated projections are checked before synthesis and again after it. Saved history remains a private snapshot, not a renewed permission grant. Provider permission mirroring remains lease/reconciliation based.
- Each request has bounded prompt size, output size and runtime. The model deadline is at most 90 seconds, including cold startup. The browser and authenticated gateway allow 120 seconds for answer POSTs (ask/debug/chat), leaving time for retrieval and validation; ordinary API requests keep their 60-second deadlines and cancellation remains active. Excessively long questions/context are rejected with an instruction to shorten them. Concurrency is initially one answer per backend process; overload produces a visible retry message. Load test the actual deployment before raising limits.
- Exact quote validation establishes that a citation exists; it does **not** establish semantic entailment, correctness of arithmetic, or completeness of retrieval. No confidence percentage is fabricated. Review representative customer questions, including disagreements, negation, dates and structured data.
- Retrieval is a bounded excerpt search. It is not a full dataset analytics engine, a whole-corpus summarizer, a general web crawler or an autonomous action agent. Repository URLs in the debug form remain references; selected GitHub App repositories are ingested through the connector.
- Digital documents and supported structured/code formats use existing adapters. Scans/images need configured native OCR. Difficult PDF layouts can require review. Audio/video need an installed trusted transcription adapter. Arbitrary unsupported files fail safely; no invented extraction or transcription is substituted.

## Verification and public-launch gates

### Executed in this workspace

| Check | Result |
| --- | --- |
| Full Python suite with disposable PostgreSQL | 465 passed, 2 skipped (native OCR requires Tesseract), 1 existing LangChain deprecation warning |
| Frontend coverage suite | 128 passed; 84.87% statements and 88.87% lines, run with one worker on this constrained host |
| Frontend lint, typecheck and production build | Passed |
| Python Ruff, mypy and Bandit | Passed |
| Locked Python dependency audit | No known vulnerabilities reported |
| Real pre-embedding application/browser harness | 118 checks passed across 101 fixture inputs, including expected safe rejections; no orphan rows or generated vectors |
| Real local-model application/browser harness | 9 checks passed: uploads, automatic embeddings, dense retrieval, policy/CSV/JSON answers, follow-up and saved persistence, abstention, account isolation and mobile layout |
| Native application database | Private backup created, existing `0004 → 0005` migration applied, records in all 16 application tables preserved, `alembic check` passed |
| Read-only local launch preflight | All six local prerequisites passed |
| Native production startup | Backend health 200, sign-up page 200, static assets 200, signed-out API 401; no completed user sign-in asserted |

The installed local answer pin and automatic embedding are enabled in the ignored native backend environment; existing credentials were preserved. The native frontend has the explicit backend URL and public origin. The model service listens on loopback. The [sanitized local-model smoke results](answer-results/local-smoke.json) contain only synthetic fixture questions, answers and quotes. Full traces/logs/screenshots remain in ignored `.local/answers/` and `.local/preembedding/`.

Docker backend/frontend builds, existing migration startup, health, private model inventory, native-host relay inference, blocked model-management/method/query routes and the six-prerequisite preflight were executed. The existing Docker source was explicitly embedded using its trusted stored owner and the pinned model. Database and upload volumes were preserved. The plain CPU model sidecar failed first on memory and then latency in this Docker Desktop VM; the active host relay resolves that local deployment limitation. Initial overlapping browser/test jobs also timed out under memory pressure; final suites are run separately with constrained concurrency. Live Clerk sign-in, external OAuth consent/delivery, deployed-domain behavior, backup restoration, production load and customer-wide answer quality remain unverified. Browser identity aliases stay confined to isolated test checkouts.

Automated tests cover upload persistence, all four native connector workflows with mocked external HTTP, validated parsing, prepared handoff, retrieval identity/owner/lease restrictions, exact answer quoting, provider failures, contextual chat, snapshot restoration, removal/restoration and source changes during generation. External consent/authentication is a separate boundary.

Run the live local browser harness only against a **separate disposable database ending `_test`**. It applies migrations and resets that test database; it must never use application data. Finish the production build first. The harness uses real Next/FastAPI/PostgreSQL/workers/local models, and test-only external Clerk aliases in an ignored checkout without environment files.

```bash
TEST_DATABASE_URL='<separate disposable PostgreSQL URL ending _test>' \
FIXFLOW_BROWSER_TOOLS='<installed Playwright package directory>' \
FIXFLOW_BROWSER_PRODUCTION=true \
GENERATION_OLLAMA_URL=http://127.0.0.1:11435 OLLAMA_URL=http://127.0.0.1:11434 \
RETRIEVAL_TIMEOUT_SECONDS=20 \
node tests/browser/run-answers.mjs
```

It checks browser uploads, real embedding/indexing, dense retrieval, policy/CSV/JSON answers, cross-document rule application, conditional exceptions, a numeric comparison and a supported recommendation, exact citations, follow-up persistence, saved quotes, missing-evidence abstention, foreign-account exclusion and 320px/390px layout. Production mode builds the isolated test frontend before starting its server; dev mode remains available when the flag is omitted. Evidence is written to `.local/answers/` (ignored), including JSON results, logs, trace and screenshots. The pre-embedding harness remains separate and explicitly disables both inference transports.

Before public launch, complete these checks on the deployed environment:

1. Live Clerk production sign-in/sign-up, gateway routing, two-account isolation and origin enforcement on the real domain.
2. Live OAuth/App consent, selected-resource sync, revocation and quota behavior for each connector advertised as available. Keep unconfigured providers disabled.
3. HTTPS termination, private backend/DB/Ollama network, durable upload/model volumes, least-privileged DB role, preserved vault key and tested backup restoration.
4. Model warm/cold latency, concurrent requests, upload/retrieval quotas and abuse controls at the hosting edge; alerting for worker failures, indexing failures and model unavailability.
5. A representative customer answer-quality evaluation with explicit gold answers/quotes, abstention cases, malicious content, PDF extraction cases and numeric/data questions. Establish acceptance thresholds before promising universal accuracy.

No paid API, external provider consent or live Clerk verification is implied by local mocked-boundary tests. Do not publish the test aliases, identity cookie or generated test checkout.
