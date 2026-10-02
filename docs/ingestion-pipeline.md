# Validated ingestion pipeline

The live worker and CLI use the same pipeline, ending at validated chunks. Existing keyword retrieval and optional embedding services remain separate. No embedding, transcription, VLM or diagnosis model is configured by this implementation.

## Boundaries

| Module | Responsibility |
| --- | --- |
| `backend/schemas/pipeline.py` | Validated Source, ContentProfile, semantic Block, Section, Asset, provenance, relationships, quality, CanonicalDocument, Concept, Chunk and parent/result contracts |
| `backend/processing/pipeline/inspection.py` | SHA-256, bounded bytes, content signatures, Office archive checks, PDF text/pages/positions, modality selection |
| `parsers.py` | Replaceable Parser contract, native adapters, PDF geometry/table adapter, conditional Tesseract adapter, advanced callback contract |
| `canonical.py` | Applicable quality measurements, escalation decisions, hierarchy and evidence relationships |
| `concepts.py` | Structural topic extraction, conservative evidence-backed alias merging, OKF generation/validation and index bundle |
| `chunks.py` | Modality routes, structural boundaries, oversized-prose refinement, tokenizer adapter, context, parent/neighbor links and validation |
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
| CSV / JSON / YAML / XML | Rows or paths/subtrees; repeated table headers; safe XML and bounded YAML reject entities, cycles, duplicate keys and non-finite numbers |
| Python / other source code | Python AST functions/classes; other languages retained atomically with an explicit warning, requiring a language adapter when oversized |
| Logs / email / VTT, SRT | Trace/timestamp records, MIME messages, speaker/time segments |
| Audio / video | Explicit failure unless a trusted transcription adapter is configured |

Quality reports measure available page/text coverage, garbling, OCR confidence, positional overlap/reading-order evidence, duplicate content, table and heading integrity. Unavailable metrics remain `null`. Simple column geometry is deterministic, not a general visual-document understanding model. Scanned tables, complex equations, irregular layouts and caption associations can require a stronger adapter or review; warnings state what native/OCR output cannot establish. Unsupported or critically invalid output fails safely instead of producing fabricated knowledge.

## Chunks and updates

Small coherent concepts stay whole. Multi-section or multimodal concepts use structural blocks; only oversized prose uses sentence/lexical-topic refinement, then a token boundary. Tables retain headers and row ranges; code functions and trace records are not broken to force a budget. Oversized atomic code/records fail explicitly. Merging is limited to compatible units under the same concept/parent. Default overlap is zero.

Raw source text is separate from deterministic retrieval context. Every chunk stores its concept, evidence block IDs, provenance, section path, content hash and token count; explicit parent containers and reciprocal neighbor links support expansion later. Equivalent wording in separate subsections retains separate context. Alias resolution requires explicit names/aliases, the same structural parent and identical substantive evidence; matching names alone do not merge concepts.

Registration records source identity, sanitized filename, MIME hint, extension, size, SHA-256, version, UTC timestamp and an opaque `source:<id>` URI before processing. Content inspection validates the MIME/profile. Successful duplicates reuse validated output. `force=true` rebuilds; `update_source_id=<owned UUID>` updates a source under its advisory lock. A changed source is reparsed to establish structure, while unchanged concept/chunk dependencies reuse artifacts and preserve vectors. Unrelated sources are untouched. Failed updates retain the previous valid corpus/artifact. Source/block/canonical/concept/chunk hashes and block→concept→chunk dependencies are persisted.

Alembic `0003` adds registration metadata and the source-owned `ingestion_artifacts` JSONB table without replacing existing tables/data. Run `myenev/bin/alembic upgrade head` before starting the new worker.

## Configuration and safety

All fields in `PipelineConfig` are validated. Native settings use `PIPELINE__FIELD` environment variables or a `PIPELINE` JSON object; nested variables override corresponding JSON values. Examples are in `backend/.env.example`. Compose forwards `PIPELINE` plus the documented OCR/tokenizer/budget variables from root `.env`.

Default tokenizer `utf8_bytes` counts UTF-8 bytes as a conservative surrogate budget; it does not claim model tokens. When a tokenizer is selected, set `PIPELINE__TOKENIZER=tiktoken` and `PIPELINE__TOKENIZER_ENCODING` to its explicit encoding, or inject a `Tokenizer` implementation in the core runner. Enriched retrieval text counts toward the budget; no embedding/model call is needed.

Defaults: 1,024 maximum / 16 minimum budget units, 50 table rows per group, 60 seconds checked parser budget, 180 seconds enforced whole-process deadline, 1 GiB Linux address-space limit, 128 MiB result envelope, 1,000 pages, 20,000 blocks, 5 million extracted characters and 10,000 chunks. Existing API upload limit remains 50 MiB; Office archives have 2,000 entry / 100 MiB expansion limits. Native OCR defaults off; Docker installs Tesseract English/Poppler and enables OCR only as needed. Native OCR requires those system tools and language data before setting `PIPELINE__OCR_ENABLED=true`.

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

On this workspace, that small fixture produced two concepts/three chunks in 0.979 seconds initially and a 0.635-second median over three cached runs. Spawn/import overhead is included; these numbers are a smoke benchmark, not a throughput guarantee. Events confirmed cached runs skipped inspection and parsing.

Golden fixtures and regression tests are in `backend/tests/fixtures/pipeline`, `backend/tests/test_pipeline.py`, `backend/tests/test_pipeline_database.py` and `scripts/tests/test_run_pipeline.py`. PostgreSQL tests require a disposable `_test` database; real OCR tests require the installed binaries and language data. CLI export, routing, quality escalation, reading order, native tables, unsafe input, canonical/OKF/chunk gates, provenance, force/cache, source updates, owner isolation and rollback are covered.
