# Parsing pipeline (contract 3, engine 3.0.0)

The processing boundary is validated canonical parsing output. No OKF generation, semantic concept extraction, chunking, embedding, retrieval, or answer generation follows it.

1. `uploads.py` validates names, extensions, payload signatures/UTF-8 and the 50 MiB limit, then streams into generated private directories. Connector adapters produce an owner-scoped `RawSourceEnvelope` and use generic source registration.
2. `routes.py` accepts the source with HTTP 202. PostgreSQL records `uploaded`; acceptance does not mean parsing completed.
3. `ingestion.py` resumes durable pending jobs, uses advisory locks to prevent duplicate processing, derives ownership from the trusted source row, and checks private-path containment and the registered content hash.
4. `execution.py` spawns a killable process with a deadline, bounded result size, and Linux memory limit. Parsing happens outside the async request loop.
5. `inspection.py` detects supported content and checks signatures/archive bounds. `parsers.py` routes native → layout → optional OCR according to configured priorities. Installed trusted adapters may extend parsing; provider calls and credentials do not enter parsers.
6. `canonical.py` measures quality and builds ordered blocks, sections, relationships, assets and provenance. Unknown quality measurements remain null. Structured data keeps paths/types; code keeps available syntax/line evidence; tables retain cells; EML retains ordered headers in metadata.
7. `validation.py` independently reconstructs the Pydantic canonical contract, verifies source authorization references/context, deterministic document identity, engine version and source/canonical hashes. Cached artifacts receive the same validation.
8. `repositories/pipeline.py` stores the canonical JSONB artifact and one whole-document text projection transactionally. It writes no chunks/vectors. Successful sources reach `parsed`; failed updates keep the last valid artifact/document and the raw upload.

Current source lifecycle: `uploaded → processing → parsed`, or `failed`. `chunked` is accepted only as an old resumable parsing job. Old `ready_for_embedding`, `embedding`, and `indexed` rows remain stored for compatibility and appear as legacy processed records, with retrieval unavailable.

`PipelineResult` contains `canonical`, `engine_version`, `config_hash`, `stage_hashes`, `warnings`, and literal `contract_version=3`. Canonical source context carries private owner/provenance evidence; offline output has a resolvable source scope without inventing an owner. The canonical document stores ordered blocks, sections, structured values, source locations, and quality evidence. Canonical block boundaries reflect source structure and have no model token budget.

`repositories/parsed.py:parsed_source(session, source_id, trusted_owner_id)` is the internal read boundary. It checks owner, active availability, parsed status, connector state and wall-clock lease **in SQL before reading the artifact**, then verifies the canonical artifact and registered source/owner/hash. No new public parsed-content endpoint has been introduced.

Existing v1/v2 artifacts supply only version history on explicit reprocessing; their old config cannot hit the new cache. Identical current-contract re-uploads reuse the source, while explicit force/update or an older contract queues parsing. Unchanged connector versions reuse original bytes and refresh permission evidence. Connector lifecycle behavior, event verification, selected-root loss, revocation/cancellation and purge remain intact.

Historical chunk/vector projections are preserved as legacy database data. The new pipeline never searches or appends to them. No schema/table is dropped and no bulk data deletion/reprocessing is performed. Strict OKF input parsing stays in `processing/okf.py`; generated OKF output and bundles have been removed.

Operational upload, parser, memory, archive, output, timeout and private-path limits remain. No tokenizers, chunk-size knobs, model pins or inference endpoints remain in active settings. OCR still requires installed Tesseract/Poppler; fixture adapters do not constitute live OCR evidence. Valid canonical output does not certify lossless extraction for arbitrary unseen PDFs.
