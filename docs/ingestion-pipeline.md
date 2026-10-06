# Validated ingestion pipeline

The live worker and CLI use the same pipeline, ending at validated chunks. Existing keyword retrieval and optional embedding services remain separate. No embedding, transcription, VLM or diagnosis model is configured by this implementation.

## Boundaries

| Module | Responsibility |
| --- | --- |
| `backend/schemas/pipeline.py` | Validated Source, ContentProfile, structural Block, Section, Asset, provenance, relationships, quality, CanonicalDocument, hierarchical Concept, AtomicUnit, UnitSlice, Chunk and parent/result contracts |
| `backend/processing/pipeline/inspection.py` | SHA-256, bounded bytes, content signatures, Office archive checks, PDF text/pages/positions, modality selection |
| `parsers.py` | Replaceable Parser contract, native adapters, PDF geometry/table adapter, conditional Tesseract adapter, advanced callback contract |
| `canonical.py` | Applicable quality measurements, escalation decisions, hierarchy and evidence relationships |
| `concepts.py` | Structural topic extraction, conservative evidence-backed alias merging, OKF generation/validation and index bundle |
| `chunks.py` | Modality routes, structural boundaries, oversized-prose refinement, tokenizer adapter, context, parent/neighbor links and validation |
| `units.py` / `validation.py` | Ordered atomic evidence, exact character/row/JSON-pointer selectors, independent complete coverage and the final contract-v2 handoff gate |
| `backend/repositories/prepared.py` | Authorized read and reconstruction; compares the artifact with actual SQL document/chunk projections before handing data to subsequent experiments |
| `runner.py` / `execution.py` | Stage orchestration, cache/dependencies, safe structured events, killable process execution |
| `backend/repositories/pipeline.py` | Atomic source-scoped PostgreSQL projections and complete JSONB artifact |

Parsers normalize into the canonical contract. Downstream stages do not consume parser-specific objects. Canonical validation rejects missing references, hierarchy errors, invalid provenance and corrupt structures/hashes before OKF generation. Generated concepts follow the [official OKF v0.2 specification](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md); only `type` is universally required. This producer additionally requires grounded source references, machine generation metadata and a draft lifecycle, and never fabricates verification. Uploaded frontmatter is preserved separately under `original_frontmatter`. Broken source Markdown links are warnings; hard schema/evidence errors stop processing.

## Current parser routes

| Input | Default path / limits |
| --- | --- |
| Digital PDF | Native text, escalating to pdfplumber geometry/columns/tables when quality requires it; page and bounding-box provenance |
| Scanned PDF / PNG, JPEG, TIFF | Conditional Tesseract OCR and bounded Poppler rendering; confidence, page, pixel bbox and original asset reference |
| DOCX / PPTX / XLSX | Safe native OOXML; headings, presentation slide order, table cells, sheet/range, embedded media references; formula text/cached values are data, never executed |
| HTML / Markdown / text | DOM / Markdown AST / paragraphs; tables, code, headings, quotes, lists and available equation text retained |
| CSV / JSON / YAML / XML | Rows or paths/subtrees; repeated table headers; YAML values use JSON-compatible serialization and comments remain quoted, searchable evidence; XML keeps the complete original root, attributes and mixed text. Safe XML and bounded YAML reject entities, cycles, duplicate keys and non-finite numbers |
| Python / other source code | Python AST functions/classes; other languages retain exact source and conservative lexical declaration evidence; oversized code uses reconstructable continuations |
| Logs / email / VTT, SRT | Trace/timestamp records, MIME messages, speaker/time segments |
| Audio / video | Explicit failure unless a trusted transcription adapter is configured |

Quality reports measure available page/text coverage, garbling, OCR confidence, positional overlap/reading-order evidence, duplicate content, table and heading integrity. Unavailable metrics remain `null`. Simple column geometry is deterministic, not a general visual-document understanding model. Scanned tables, complex equations, irregular layouts and caption associations can require a stronger adapter or review; warnings state what native/OCR output cannot establish. Unsupported or critically invalid output fails safely instead of producing fabricated knowledge.

## Chunks and updates

Engine **2.1.2**, artifact contract **2**, extends the existing pipeline (initial contract-v2 engine: 2.0). Top-level sections and nested substantive sections form deterministic concepts; parents retain full descendant evidence for expansion, while `direct_block_ids` partition the canonical document exactly. No model infers topics or semantic relationships. Concepts are structural subjects, functions, key subtrees or source-record sections, with conservative, evidence-backed alias resolution. A headingless prose document remains one topic.

Each canonical element becomes one ordered `AtomicUnit`, including headings. Paragraphs, lists, code definitions, complete objects and tables remain intact as original canonical evidence. Chunks contain full units or exact `UnitSlice` references. Child concepts own their evidence; parents do not duplicate it in retrieval chunks. Containers retain relationships for expansion. Chunks follow global canonical order, even when headings are revisited; neighbors cross concept boundaries within the same source.

The chunker tries a whole coherent concept, then child-section/direct-element boundaries. Oversized prose prefers sentences/blank lines, uses the existing deterministic lexical Jaccard refinement, and finally word/character boundaries. Oversized lists pack complete items where possible. Oversized Python definitions/classes split only between AST statements/definitions; fragments retain exact source lines and intersecting symbol paths. Compound statements, oversized strings, non-Python code and stack traces use exact, ordered continuations when higher-level boundaries cannot fit. Markdown Python fences resolve lines to code rather than fence delimiters. Non-Python declarations are lexical evidence, not a full language AST or syntax certification.

Large tables repeat the exact headers with each row group. Table identity is the atomic-unit ID; its complete schema/content and section/caption evidence resolve through the canonical artifact. Row selectors are one-based and inclusive. JSON/YAML splitting traverses complete key subtrees and contiguous array groups; keys, escaped RFC-style JSON pointers and original absolute array indices remain resolvable. JSON pointer paths are relative to the complete atomic serialization; the unit's original path supplies document context. Array selectors are zero-based and end-exclusive. Oversized scalars use exact canonical character continuations with path context; deep JSON/YAML sources retain escaped paths and object/array ancestor types rather than inheriting OKF metadata depth limits. Large XML roots use SAX-derived structural anchors and exact continuations that retain all original wrappers, attributes, mixed text and tails. Huge table rows/cells/headers use schema-addressed continuations; complete schema and row/column identity resolve through the atomic unit.

`raw_content` is canonical evidence; `retrieval_content` adds deterministic concept/section/alias context. Future embedding experiments should deliberately choose their input from this validated contract. `token_count` is the configured budget measurement, **UTF-8 bytes by default**, not a claim about an embedding model's tokens. Maximum remains **1,024 bytes including context**. The existing 16-unit minimum is a merge/refinement preference: a complete short atomic element/object is valid below it. Continuation tails can be smaller than the preferred minimum when their complete group reconstructs the original unit; ordinary partial fragments retain the existing minimum gate. No sliding-window overlap is introduced; only necessary table headers and structural context repeat. `boundary_kind` exposes statement/sentence/list/row/subtree boundaries and emergency `hard_size` fragments.

Every chunk carries source and canonical-document identity, concept, container, global order, previous/next IDs, block provenance, exact atomic slices, authorization scope, hash, byte/character lengths and source context. SQL metadata additionally carries the UUID concept-document FK, source hash/version, provider version/modification time when supplied, engine/config version and an enriched-text hash. `line_scope` distinguishes exact source fragment lines from a normalized unit's broader original source range. Canonical character offsets never imply identical original YAML/table serialization. The canonical document ID names the source-wide artifact; SQL `document_id` names its concept projection. Neither is a vector identity.

Coverage is independent of the splitter: reconstruct each fragment directly from immutable canonical units, require equality with stored raw chunk text, then cover every meaningful character interval, table row or structured leaf. Headings must be grounded in retrieval context or explicit canonical evidence when the context budget requires a compact reference. Continuation groups independently verify complete, adjacent character ranges, ordered indexes/counts, full-unit hashes and structural locations. Missing spans/rows/leaves, mixed representations, altered keys, incorrect lines/symbols, unauthorized references, duplicate content positions, corrupt hashes, reordered chunks or inconsistent links fail the gate. Whitespace normalization is allowed; format delimiters are not treated as standalone knowledge. Original bytes remain in private source storage. This proves canonical→chunk completeness; parser quality measurements and fixture checks remain separate evidence for raw→canonical fidelity.

`validate_result` runs before persistence mutates any rows. Persistence additionally verifies the registered hash and trusted source owner. `prepared_source(session, source_id, owner_id)` applies owner/availability/connector/lease filters **before reading private artifacts**, validates contract v2, and verifies document/chunk projections against the artifact. It returns `None` for unavailable/foreign sources and rejects corrupt or legacy data. A status label alone is insufficient for this handoff.

Registration records source identity, sanitized filename, MIME hint, extension, size, SHA-256, version, UTC timestamp and an opaque `source:<id>` URI before processing. Content inspection validates the MIME/profile. Successful duplicates reuse validated output. `force=true` rebuilds; `update_source_id=<owned UUID>` updates a source under its advisory lock. A changed source is reparsed to establish structure, while unchanged concept/chunk dependencies reuse artifacts and preserve vectors. Unrelated sources are untouched. Failed updates retain the previous valid corpus/artifact. Source/block/canonical/concept/chunk hashes and block→concept→chunk dependencies are persisted.

The additive migration history remains `0001`–`0005`, with one head. Contract v2 uses existing JSONB artifacts and document/chunk metadata; **no new migration or schema change is required**. Existing composite chunk/document/source FKs, hash uniqueness, owner filtering, keyword index and atomic transactions remain authoritative.

Manual-source duplicates are owner/hash scoped and reuse the existing source. Distinct connector resources remain owner/provider/account/resource scoped even when bytes match. Hashes detect exact content; similar content is not merged. Changed successful versions replace old projections under the existing policy; failed updates retain the previous valid artifact but exclude failed sources from retrieval. Saved-history snapshots remain independent. This is not a new historical-version archive.

Old contract-v1 artifacts and the reachable manual JSONL importer remain **LEGACY**, retaining keyword functionality. They cannot pass the prepared handoff. Re-uploading old manual content queues a v2 upgrade without changing the source ID; the existing force/update options also work. A connector's next registration queues old artifacts for reprocessing. The legacy `processing/loaders.py` / `chunking.py` path remains 3,000 characters with 400-character overlap; no production upload or connector uses it. `services/ingestion.py:load_documents` is a legacy/test helper. The export CLI uses the production pipeline but its ownerless offline output must be registered and assigned a trusted owner before production persistence.

## Configuration and safety

All fields in `PipelineConfig` are validated. Native settings use `PIPELINE__FIELD` environment variables or a `PIPELINE` JSON object; nested variables override corresponding JSON values. Examples are in `backend/.env.example`. Compose forwards `PIPELINE` plus the documented OCR/tokenizer/budget variables from root `.env`.

Default tokenizer `utf8_bytes` counts UTF-8 bytes as a conservative surrogate budget; it does not claim model tokens. When a tokenizer is selected, set `PIPELINE__TOKENIZER=tiktoken` and `PIPELINE__TOKENIZER_ENCODING` to its explicit encoding, or inject a `Tokenizer` implementation in the core runner. Enriched retrieval text counts toward the budget; no embedding/model call is needed.

Defaults: 1,024 maximum / 16 preferred minimum budget units, 50 table rows per group, 60 seconds checked parser budget, 180 seconds enforced whole-process deadline, 1 GiB Linux address-space limit, 128 MiB result envelope, 1,000 pages, 20,000 blocks, 5 million extracted characters and 10,000 chunks. Existing API upload limit remains 50 MiB; Office archives have 2,000 entry / 100 MiB expansion limits. Native OCR defaults off; Docker installs Tesseract English/Poppler and enables OCR only as needed. Native OCR requires those system tools and language data before setting `PIPELINE__OCR_ENABLED=true`.

Trusted server extensions use `adapter_factories` entries `installed.module:factory`, returning a Parser, with their names included in `parser_priority`. Tiers remain cheapest-first. Supply all additional fields through the native environment or Compose's `PIPELINE` JSON, for example `{"adapter_factories":["my_adapter:create"],"parser_priority":["native","layout","ocr","advanced"],"adapter_revision":"2"}`. Adapter configuration is never accepted from uploaded content. Increment `adapter_revision` after changing an installed adapter to invalidate old output. Adapters must implement their own external request timeouts and normalize real evidence; no paid calls occur by default.

The worker runs in a spawned killable process; deadlines terminate its decoder process group on Linux. Results cross the boundary as bounded validated JSON. This limits resource consumption but is not an OS security sandbox. Fixed decoder argv never uses a shell, XML rejects external entities, YAML uses a safe loader, uploads have private generated paths, and structured logs omit raw text. Existing gateway ownership, safe client errors, transaction rollback and job recovery remain intact.

## Run and inspect

From the repository root, after installing existing requirements:

```bash
myenev/bin/python -m scripts.run_pipeline backend/tests/fixtures/pipeline/guide.md --out .local/pipeline-demo
```

The private export contains OKF indexes/concepts and `pipeline.json` with canonical evidence, chunks, graphs, hashes and dependencies. This local export is not production storage; live uploads persist in PostgreSQL. Repeating the command uses the cache; `--force` rebuilds and can recover incompatible exports. Use `--strict-okf` for an uploaded standalone OKF concept, and `--source-id` when the input location changes but identity should remain stable. Output files are replaced individually, not as a whole-directory transaction.

```bash
myenev/bin/python -m scripts.run_pipeline backend/tests/fixtures/pipeline/guide.md --out .local/pipeline-demo --force --benchmark 3
```

Golden fixtures and regression tests are in `backend/tests/fixtures/pipeline`, `backend/tests/test_pipeline.py`, `backend/tests/test_pipeline_database.py`, `backend/tests/test_pipeline_integrity_audit.py`, `backend/tests/test_prepared_pipeline.py` and `scripts/tests/test_run_pipeline.py`. PostgreSQL tests require a disposable `_test` database; real OCR tests require installed binaries and language data. Tests cover deterministic corpus output, three complete source→database→keyword-search traces, idempotency, versions, private ownership, rollback, old-contract upgrade, tampering, independent loss detection, tables, escaped structured paths, Unicode, repeated text, large Python methods and source-line citations.

## Handoff boundaries

Ready for embedding **experiments on newly validated v2 data**. No embedding-model selection, generation, vector retrieval, reranking or LLM generation was added. Pre-existing optional embedding/vector modules remain separate and unconfigured in this verification. Live provider HTTP/consent and live Clerk login were not exercised; connector tests mock only external HTTP. Native OCR checks require local Tesseract/Poppler. Engine 2.1.2 handles oversized code/scalars/table rows/cells/XML with validated, lossless continuations; measured comparisons and current limitations are in [difficult-content.md](difficult-content.md). Transcription and uncertain layouts still require an adapter/review. Detected rotated/gutter-spanning text is explicitly excluded from ready/prepared output. Operational resource caps remain enforced. Canonical evidence coverage does not certify arbitrary unseen parser outputs as perfect.

## Verification record (2026-10-06)

Audit baseline: branch `main`, commit `caf9f4e05bde1ad6ebef91469f2b95caacfe08b0`, initially clean. Before changes, live uploads/paste and connectors already converged on `ingest_source` → `execute_pipeline` → `run_pipeline` → canonical → top-level concepts → temporary chunk units → PostgreSQL. Missing pieces were nested concept ownership, persisted atomic units, exact fragment selectors, independent coverage, trusted upload context and a verified reconstructed handoff. Verified parser gaps included XML root attributes and YAML comments; heading context for differently titled uploaded OKF and repeated-evidence line references also received regression fixes.

Smallest design: extend existing Pydantic contracts and JSONB, retaining parser routing, source UUIDs, transactions, current budget configuration, keyword queries and frontend contracts. No parser framework, ORM, database schema or product features were replaced.

| Check | Executed result |
| --- | --- |
| Full `myenev/bin/python -m pytest` with disposable PostgreSQL | **271 passed, 2 skipped**, 83% combined branch coverage; one legacy LangChain deprecation warning. First full run found the uploaded-OKF heading regression; it was fixed and the full rerun passed. |
| Final pipeline/corpus/database/connector regression run after strengthening SQL projection and line-precision checks | **123 passed, 2 OCR skips**; the prepared corpus now contains 53 tests, including parent corruption, normalized YAML/CSV source ranges and trailing code lines. |
| `npm test` | **115 passed** |
| `npm run typecheck`, `npm run lint` | **Passed** |
| Ruff and mypy over `backend scripts` | **Passed**, 89 Python files type-checked |
| Bandit over production `backend scripts` | **Passed**; final prepared-reader change also scanned separately |
| `alembic check` | **No new upgrade operations**; test fixture exercised downgrade → upgrade → repeat upgrade through head `0005` |
| Three manual API → actual PostgreSQL → prepared handoff → keyword-search traces | **Passed**; nine beginning/middle/end queries each returned the expected chunk at rank 1; foreign-owner results were empty |
| Live provider HTTP/consent, live Clerk browser login, browser/build | **Not executed** for this backend substrate change; frontend/API response contracts remain compatible |
| Real OCR checks | **Skipped** because native Tesseract/Poppler prerequisites are unavailable |

All database mutation/verification used `fixflow_pipeline_test`; application data was not migrated or reprocessed. Safe fixture artifacts, original source files, actual UUID/FK chains, selectors and generated text are available locally in ignored `.local/pipeline-readiness/evidence.json` and the three `*-pipeline.json` files. Test fixtures may reset the disposable database; the JSON evidence is independent of its later lifecycle.

| Manual trace | Canonical elements / atomic units | Concepts | Chunks | Median / max enriched UTF-8 bytes | Coverage | Generated embeddings |
| --- | --- | --- | --- | --- | --- | --- |
| md | 6 / 6 | 3 | 3 | 76 / 80 | 100.0% | 0 |
| py | 2 / 2 | 1 | 2 | 927.5 / 1014 | 100.0% | 0 |
| csv | 1 / 1 | 1 | 5 | 986 / 1021 | 100.0% | 0 |

Each trace had zero uncovered elements and zero duplicate content positions. Example middle-sentinel chains:

- **md**: source `6a993b11-dde0-4d6a-beb7-2b356bc803ff` → canonical `d-892b8c2a6bfce796bb29f874136c78f7` → concept `concepts/details-7e522d3259a6` → atomic `u-a45fc513fcf7cc19a63fd24fd1c2d300c47f545b` (source lines 7–7, canonical characters [0, 27)) → chunk `c-0f04ac2d3b37f1a3bf70548704501505bd9d0136` → SQL document `29b315f7-ca10-5270-8143-24e1631666a4` / SQL chunk `aabfc179-e085-5c4f-bfc8-2f42275e8a59`.
- **py**: source `c895fdc6-ff7a-42d2-a5bc-bc9c1cbf6c7f` → canonical `d-7ce004642558f22d19fab1a7200f760c` → concept `concepts/trace-763874c616d1` → atomic `u-31a28355245e9d34a8bff910307fb7054e6d99fc` (source lines 1–52, canonical characters [0, 963)) → chunk `c-569c66ceff884c890f21ef8333f2d44de4190b15` → SQL document `6306900a-cbaf-51fe-bfdf-918ee49d8c81` / SQL chunk `610af8da-6452-528b-92c6-ef6b87a229af`.
- **csv**: source `73dc1556-47dc-4aaa-96af-8e093d5af283` → canonical `d-22f063e60c74c6a7ab8b606fcaf20996` → concept `concepts/manual-trace-csv-86f95a014757` → atomic `u-4ac75b9645ead7893cd293d215647ec2673fc3cf` (rows 42–61) → chunk `c-7845b6d16928f858f7f851550ba3b374593d1b23` → SQL document `8d578948-db89-5a97-a87c-ba962158febc` / SQL chunk `b7fb13fd-4d16-5ab8-95d4-dfa23f70b453`.

Changed files: `.env.example`, `backend/.env.example`, `AGENTS.md`, `SKILLS.md`, this document; `backend/api/routes.py`; `backend/schemas/pipeline.py`; `backend/services/{ingestion,connector_sources}.py`; `backend/processing/pipeline/{context,parsers,concepts,chunks,runner,units,validation}.py`; `backend/repositories/{pipeline,prepared}.py`; `backend/tests/{test_pipeline,test_pipeline_database,test_prepared_pipeline}.py`. The new modules are `units.py`, `validation.py`, `prepared.py` and `test_prepared_pipeline.py`. No migrations, database model definitions, frontend code, dependency versions, embedding generation, vector queries, rerankers or LLM code changed.

## Final pre-embedding browser/system verification

The current engine 2.1.2/native parser 5 report is [preembedding-validation.md](preembedding-validation.md). It supersedes the historical stage-specific test counts above: 101/101 browser fixture outcomes, 118 real Next application checks, 356 backend passes (two live OCR skips) and 118 frontend passes. It documents original→canonical checks separately from canonical→chunk coverage, fixes for email-header provenance/mobile filenames/bounded polling, prepared handoff rejection tests, and external/OCR/legacy limitations. Pre-existing optional embedding modules remain unconfigured in this verification; no vectors were generated. Reproduction commands and safe parser/splitter extension instructions are in [SKILLS.md](../SKILLS.md) and [AGENTS.md](../AGENTS.md).
