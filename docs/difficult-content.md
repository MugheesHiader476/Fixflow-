# Difficult-content hardening — contract v2, engine 2.1.1

Measured on 2026-10-06, starting from main / 7127c32. No embedding model, generation, vector retrieval, reranking or LLM calls were added. Existing optional embedding modules remain unchanged.

## Current failure points and minimal architecture

The production path stays: authenticated registration / private raw file / hash and version → format inspection and parser cascade → canonical ordered blocks → OKF concepts → atomic units → content-specific boundaries → continuation fallback → reciprocal hierarchy/neighbor links → independent coverage/reconstruction validation → transactional PostgreSQL → ready_for_embedding → authorized `prepared_source`.

Reproduced failures were oversized non-Python code, atomic Python statements/headers, JSON/YAML scalars, XML roots, table rows/headers, source nesting beyond the mistakenly reused 32-level OKF limit, huge numeric conversion and machine-float overflow/precision loss. Geometric PDF extraction could report confidence for rotated or gutter-spanning content; those cases now explicitly require review. Raw input is retained on all processing failures.

## Verified implementation map

| Stage | Existing/current entry points | Difficult-content change |
| --- | --- | --- |
| Raw registration | `uploads.save_upload`, source repositories and connector registration; `ingestion.ingest_source` | Unchanged private raw files, hashes, versions and authoritative owner |
| Parser routing | `inspection.inspect`, `runner.run_pipeline`, `NativeParser.parse` / `LayoutParser.parse` / OCR adapter | Separate source-tree normalization, precise numeric serialization, lexical code symbols, uncertain-layout exclusion |
| Canonical | `canonical.quality`, `canonical.canonicalize`, `CanonicalDocument` | Preserve ordering/provenance and fail closed on flagged layout |
| Concept | `concepts.extract_concepts`, `resolve_concepts`, `validate_okf` | Unchanged OKF hierarchy/ownership; source data no longer inherits frontmatter depth bound |
| Atomic unit | `units.atomic_units`, `character_slice` | Unchanged complete canonical evidence plus exact slice references |
| Chunk | `chunks.chunk_concept`, `split_block`, `continuation_units`, `splitting.*` | Preferred existing structural cuts, content-specific lower-level boundaries, final reconstructable continuations |
| Validation | `validation.validate_result`, `chunks.validate_chunks`, `units.coverage_report`, `validate_continuations` | Independent full-unit/group/offset/hash/path/order/auth verification |
| Persistence/status | `repositories.pipeline.persist_result`, `ingestion.ingest_source` | Existing transaction/projections and ready_for_embedding gate; no schema change |
| Prepared handoff | `repositories.prepared.prepared_source` | Existing owner/availability/status checks, complete artifact validation and SQL projection comparison |
| Legacy | `processing/chunking.py`, `scripts/import_jsonl_to_db.py` | Manual legacy path retained; not v2, never silently promoted into prepared handoff |

## Candidate experiment matrix

The reproducible harness measured 35 complete source runs (33 generated difficult inputs and two actual repository documents), 218 atomic candidate measurements, and 18 PDF parser measurements. Candidate content budget: 768 UTF-8 bytes; production enriched limit: 1,024 bytes. Latency is local per-prototype median, including a deterministic repeat, with existing process caches; it is not a service SLA. “Quality” below means source-derived boundary alignment, not a model-scored coherence estimate. Reconstruction for subtree/row candidates is structural; continuation reconstruction is exact canonical character equality.

| Content type | Candidate strategy | Coverage | Reconstruction | Structural correctness | Chunk quality | Duplication | Median latency ms | Complexity | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| prose | sentences-only | Rejected (0/4) | 0/4 | Oversized atom rejected | — | 0 unexplained | 0.222 | stdlib / existing libraries | Insufficient alone |
| prose | clauses-continuation | 100% on 4/4 | 4/4 | Exact selectors; fragments explicit | 25.0% aligned | 0 unexplained | 2.077 | stdlib / existing libraries | All pass |
| prose | lexical-only | 100% on 4/4 | 4/4 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 2.464 | stdlib / existing libraries | All pass |
| Code (python) | ast-only | 100% on 1/2 | 1/2 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 1.858 | stdlib / existing libraries | Insufficient alone |
| Code (python) | syntax-lexical-continuation | 100% on 2/2 | 2/2 | Exact selectors; fragments explicit | 50.0% aligned | 0 unexplained | 0.433 | stdlib / existing libraries | All pass |
| Code (python) | lexical-only | 100% on 2/2 | 2/2 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.700 | stdlib / existing libraries | All pass |
| Code (javascript) | lines-only | 100% on 1/5 | 1/5 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.544 | stdlib / existing libraries | Insufficient alone |
| Code (javascript) | syntax-lexical-continuation | 100% on 5/5 | 5/5 | Exact selectors; fragments explicit | 60.0% aligned | 0 unexplained | 0.340 | stdlib / existing libraries | All pass |
| Code (javascript) | lexical-only | 100% on 5/5 | 5/5 | Exact selectors; fragments explicit | 7.8% aligned | 0 unexplained | 0.737 | stdlib / existing libraries | All pass |
| Code (typescript) | lines-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.825 | stdlib / existing libraries | All pass |
| Code (typescript) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.133 | stdlib / existing libraries | All pass |
| Code (typescript) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 40.0% aligned | 0 unexplained | 0.434 | stdlib / existing libraries | All pass |
| Code (java) | lines-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.976 | stdlib / existing libraries | All pass |
| Code (java) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.170 | stdlib / existing libraries | All pass |
| Code (java) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.524 | stdlib / existing libraries | All pass |
| Code (c) | lines-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.762 | stdlib / existing libraries | All pass |
| Code (c) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.131 | stdlib / existing libraries | All pass |
| Code (c) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 20.0% aligned | 0 unexplained | 0.413 | stdlib / existing libraries | All pass |
| Code (cpp) | lines-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 1.502 | stdlib / existing libraries | All pass |
| Code (cpp) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.209 | stdlib / existing libraries | All pass |
| Code (cpp) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 20.0% aligned | 0 unexplained | 0.458 | stdlib / existing libraries | All pass |
| Code (go) | lines-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.950 | stdlib / existing libraries | All pass |
| Code (go) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.165 | stdlib / existing libraries | All pass |
| Code (go) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.450 | stdlib / existing libraries | All pass |
| Code (rust) | lines-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.770 | stdlib / existing libraries | All pass |
| Code (rust) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.143 | stdlib / existing libraries | All pass |
| Code (rust) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 20.0% aligned | 0 unexplained | 0.413 | stdlib / existing libraries | All pass |
| json | subtrees-only | 100% on 1/2 | 1/2 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.231 | stdlib / existing libraries | Insufficient alone |
| json | path-continuation | 100% on 5/5 | 5/5 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.026 | stdlib / existing libraries | All pass |
| json | lexical-only | 100% on 5/5 | 5/5 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.016 | stdlib / existing libraries | All pass |
| json | numeric-coercion | Rejected (0/3) | 0/3 | Oversized atom rejected | — | 0 unexplained | 0.025 | stdlib / existing libraries | Insufficient alone |
| yaml | subtrees-only | 100% on 1/2 | 1/2 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.229 | stdlib / existing libraries | Insufficient alone |
| yaml | path-continuation | 100% on 4/4 | 4/4 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.246 | stdlib / existing libraries | All pass |
| yaml | lexical-only | 100% on 4/4 | 4/4 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.208 | stdlib / existing libraries | All pass |
| yaml | numeric-coercion | Rejected (0/2) | 0/2 | Oversized atom rejected | — | 0 unexplained | 1.627 | stdlib / existing libraries | Insufficient alone |
| xml | elements-only | Rejected (0/2) | 0/2 | Oversized atom rejected | — | 0 unexplained | 0.065 | stdlib / existing libraries | Insufficient alone |
| xml | sax-continuation | 100% on 2/2 | 2/2 | Exact selectors; fragments explicit | 6.7% aligned | 0 unexplained | 1.670 | stdlib / existing libraries | All pass |
| xml | lexical-only | 100% on 2/2 | 2/2 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 1.603 | stdlib / existing libraries | All pass |
| table | whole-table | Rejected (0/4) | 0/4 | Oversized atom rejected | — | 0 unexplained | 0.024 | stdlib / existing libraries | Insufficient alone |
| table | rows-only | 100% on 1/4 | 1/4 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.143 | stdlib / existing libraries | Insufficient alone |
| table | cell-continuation | 100% on 4/4 | 4/4 | Exact selectors; fragments explicit | 50.0% aligned | 0 unexplained | 0.298 | stdlib / existing libraries | All pass |
| table | lexical-only | 100% on 4/4 | 4/4 | Exact selectors; fragments explicit | 41.4% aligned | 0 unexplained | 0.742 | stdlib / existing libraries | All pass |
| Code (sql) | lines-only | Rejected (0/1) | 0/1 | Oversized atom rejected | — | 0 unexplained | 0.330 | stdlib / existing libraries | Insufficient alone |
| Code (sql) | syntax-lexical-continuation | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 100.0% aligned | 0 unexplained | 0.416 | stdlib / existing libraries | All pass |
| Code (sql) | lexical-only | 100% on 1/1 | 1/1 | Exact selectors; fragments explicit | 0.0% aligned | 0 unexplained | 0.968 | stdlib / existing libraries | All pass |

Necessary table-header repetition is measured separately from duplicate evidence in the JSON report. A single complete chunk has no interior boundary to evaluate. No semantic-score, compiler correctness or OCR accuracy is fabricated. Every final production output was independently checked for provenance, owner scope, deterministic IDs/order, byte limits, duplicate positions, complete canonical coverage and reconstructable continuation groups.

### PDF candidates

| Fixture | Candidate | Word recall | Structure / quality gate | Latency ms |
| --- | --- | --- | --- | --- |
| digital.pdf | native | 100.00% | PASS | 0.122 |
| digital.pdf | pypdf-layout | 100.00% | PASS | 0.389 |
| digital.pdf | geometry | 100.00% | PASS | 35.238 |
| columns.pdf | native | 100.00% | unresolved_reading_order | 0.161 |
| columns.pdf | pypdf-layout | 100.00% | unresolved_reading_order | 0.531 |
| columns.pdf | geometry | 100.00% | PASS | 3.632 |
| table.pdf | native | 100.00% | unresolved_reading_order | 0.188 |
| table.pdf | pypdf-layout | 100.00% | unresolved_reading_order | 1.377 |
| table.pdf | geometry | 100.00% | PASS | 4.758 |
| integrity-audit.pdf | native | 100.00% | unresolved_reading_order | 0.834 |
| integrity-audit.pdf | pypdf-layout | 100.00% | unresolved_reading_order | 10.928 |
| integrity-audit.pdf | geometry | 100.00% | PASS | 86.000 |
| difficult-layout.pdf | native | 100.00% | unresolved_reading_order | 0.388 |
| difficult-layout.pdf | pypdf-layout | 100.00% | unresolved_reading_order | 2.834 |
| difficult-layout.pdf | geometry | 100.00% | layout_uncertain, unresolved_reading_order | 21.900 |
| rotated-layout.pdf | native | 100.00% | unresolved_reading_order | 0.568 |
| rotated-layout.pdf | pypdf-layout | 98.82% | unresolved_reading_order | 2.433 |
| rotated-layout.pdf | geometry | 98.82% | layout_uncertain, unresolved_reading_order | 24.839 |

Word recall compares extracted lexical occurrences; it does not prove perfect layout reconstruction. Simple columns have an explicit expected reading-order regression. Rotated extraction lost one lexical occurrence in the layout candidates; the hard gate rejects that output, retains the original, and returns a safe layout_uncertain / needs-review reason. Headers, footnotes and captions in the difficult fixtures remain source evidence; they are not silently removed.

## Winners, reasons and deterministic fallback

| Type | Preferred strategy | Why it won | Final fallback |
| --- | --- | --- | --- |
| Prose | Existing sentence/paragraph path, then clauses and lexical boundaries | Sentence-only rejected all four extreme prose atoms; clauses preserve more punctuation boundaries with complete reconstruction | Unicode character continuation |
| Python | Existing AST statement path, then AST/lexical anchors | Complete statements remain parseable; a huge statement no longer aborts its function | Explicit code continuation with full symbol/line/unit identity |
| JS / TS / Java / C / C++ / Go / Rust / SQL | String/comment-aware lexical punctuation and conservative declarations | All tested units reconstruct; minified statements/object members/arguments survive; more structural endpoints than plain lexical windows | Code continuation inside indivisible literals/tokens |
| JSON / YAML | Existing subtrees/array ranges, then path-addressed continuations | Subtree-only rejects huge scalar values; path metadata survives where lexical-only has no structural identity | Exact canonical/source serialization continuations |
| Numeric JSON / YAML | Validate syntax and retain original numeric lexemes when coercion is unsafe/inexact | Huge integers/exponents and precise decimals survive without disabling integer-conversion safeguards or changing numbers into string values | Same path-aware continuations |
| Deep JSON / YAML | Iterative normalization, shallow path-addressed leaves and object/array ancestor types | 100-level fixtures pass; numeric keys and array indices remain distinguishable; empty containers survive | Existing safe decoder/process bounds; raw retained on failure |
| XML | Existing secure root parsing plus SAX element/text anchors | Complete atom alone cannot fit; SAX adds exact paths while preserving every wrapper, attribute and mixed-text tail | Explicit XML continuation; never advertised as standalone XML |
| Tables | Whole small table, header + row groups, then schema/row/cell-addressed continuations | Whole tables/rows reject huge cells or schemas; cell continuations cover every canonical character and preserve row/column identity | Unicode continuation with authoritative schema reference |
| PDF | Native on simple text, existing geometry on supported columns/tables | Geometry passes the complex supported fixtures that native/layout-mode text cannot safely order | Suitable configured adapter or explicit failed/needs-review; raw retained |
| OCR | Existing Tesseract adapter and common downstream contract | Native engine unavailable; deterministic adapter-output fixtures pass downstream coverage, page/bbox/confidence/provenance/auth tests | Keep unconfigured native OCR unavailable; do not fabricate text |

Tree-sitter was checked for availability and official capabilities, not installed or benchmarked. The tested lexical strategy satisfies lossless representation without new grammars/dependencies; no claim is made that it matches a full syntax tree. [Official Tree-sitter documentation](https://tree-sitter.github.io/tree-sitter/index.html). PDF comparisons use the installed libraries and their primary references: [pypdf extraction](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md), [pdfplumber](https://github.com/jsvine/pdfplumber/blob/stable/README.md). Python numeric conversion limits remain enabled: [official JSON decoder documentation](https://docs.python.org/3/library/json.html).

## Continuation contract and integrity

`UnitSlice.continuation` is additive JSONB metadata: deterministic group ID, zero-based index, total count, full-unit hash, strategy, start/end structural paths and table start/end row/column identities. Existing character_start/end are canonical character offsets, with exclusive end. Existing unit_id resolves the complete original unit; source/document/concept IDs, authorization, section, symbol paths, line scope and source version are already carried by the parent contracts.

Each group must contain exactly count references in order, cover [0, len(unit.content)) with adjacent non-overlapping ranges, and reconstruct the complete canonical string. Group identity, full-unit hash and structural locations are independently recomputed from immutable evidence, without rerunning the splitter. Fragment contents must equal their selectors. Neighbor links and container identities remain reciprocal/deterministic. Repeated text at different continuation positions receives different IDs; duplicate canonical occurrences retain all evidence references. Invalid groups fail before any database mutation and before prepared_source returns data.

Context is bounded; complete headings that cannot fit a prefix become explicit evidence. Continuations may have a short or whitespace-only tail because exact reconstruction is mandatory. Partial code/JSON/XML is marked as a continuation of a valid complete unit, not a standalone syntax fragment. Zero content overlap remains the policy. Complete table schemas resolve through the atomic unit; header text repeats when it fits, while enormous schemas use a stable reference plus explicit row/column ranges. No table fragment is an unreferenced standalone row.

Deep structured leaves retain escaped full paths and ancestor container types, preserving array-vs-object identity and empty containers. Huge numeric sources use original serialization as canonical evidence, preserving number type syntax; validation-only string conversion is discarded and is not the stored value. Duplicate keys, unsafe tags, cycles, null bytes and non-standard/non-finite constants remain rejected.

## Implementation, database and compatibility

No dependency was added. No database model or migration was changed; existing JSONB stores the additive slice declaration. Existing UUID/FK, source identity/hash/version, owner filtering, keyword index, status constraints and transactions remain unchanged. Migration head remains 0005. Existing valid v2 artifacts remain readable and independently validated; the new engine/config hash invalidates processing caches. Legacy JSONL/v1 requires explicit reprocessing from retained/re-uploaded original content and is never accepted as v2.

To reprocess an affected owned source, use the existing document upload endpoint/UI update with update_source_id and force=true, supplying its original file. This reuses source identity, checks ownership, retains the previous artifact on failure, and updates the normal projections transactionally. No automatic import or destructive version migration is introduced.

### Exact files changed

- `AGENTS.md`
- `SKILLS.md`
- `docs/ingestion-pipeline.md`
- `docs/difficult-content.md`
- `backend/schemas/pipeline.py`
- `backend/processing/pipeline/canonical.py`
- `backend/processing/pipeline/chunks.py`
- `backend/processing/pipeline/inspection.py`
- `backend/processing/pipeline/parsers.py`
- `backend/processing/pipeline/runner.py`
- `backend/processing/pipeline/splitting.py`
- `backend/processing/pipeline/units.py`
- `backend/tests/difficult_corpus.py`
- `backend/tests/test_difficult_content.py`
- `backend/tests/test_pipeline.py`
- `backend/tests/test_prepared_pipeline.py`
- `backend/tests/fixtures/pipeline/difficult-layout.pdf`
- `backend/tests/fixtures/pipeline/rotated-layout.pdf`
- `scripts/benchmark_difficult_content.py`

The existing repositories/prepared.py, repositories/pipeline.py and service/API contracts need no edits: they already call the final validator and persist/decode the complete slice metadata. New integration tests prove acceptance of validated difficult sources and rejection of corrupted/failed/foreign-owned sources.

## Formats and languages actually verified

This change exercises prose/Markdown, Python, JavaScript, TypeScript, Java, C, C++, Go, Rust, SQL, JSON, YAML, XML, CSV/Markdown tables, digital PDF, two-column PDF and PDF tables. Existing full-suite OOXML, HTML, email, transcript, log, connector and legacy-import regressions also pass. Python is AST-validated; the other named code languages are verified for lossless lexical representation and declaration provenance, not compilation or full grammar validation. Native live OCR is unverified because Tesseract is absent; deterministic scanned-PDF/image OCR-output fixtures are verified only downstream.

## Coverage, reconstruction, authorization and prepared-source results

All 35 complete corpus/project-source runs have 100% canonical coverage, no uncovered elements and zero unexplained duplicate positions. Every continuation group reconstructs its exact normalized canonical unit, including whitespace and Unicode. Deep container reconstruction is separately verified against the original parsed JSON tree. These checks do not claim perfect extraction of unseen PDFs or images.

Database regressions cover upload → worker → canonical/concept/atomic/chunk artifacts → SQL → prepared_source → keyword retrieval. Beginning/middle/end sentinels remain searchable. Reprocessing retains IDs; changed content is version-aware. Another owner receives no prepared source or search results. Invalid continuation declarations roll back projection writes, leave the source failed and retain the raw file; uncertain layouts cannot enter ready_for_embedding. Persisted vectors remain NULL in these tests.

## Three source → database traces

These controlled sources were uploaded through the authenticated API and processed by the real spawned worker into disposable PostgreSQL. Each reached ready_for_embedding; all nine exact beginning/middle/end sentinel queries found the correct source, and the other owner had no prepared handoff. Each result has 100% canonical coverage and exact continuation reconstruction. All three are source version 1, engine 2.1.1, private authorization scope `source:<source_id>`.

### JS

| Stage | Actual identity/evidence |
| --- | --- |
| Source | `83808323-77b1-4890-82fc-b6c4308967e7` |
| Canonical document | `d-9214165941b00e08c47c3995e6595301` |
| Concept | `concepts/manual-trace-js-1e5eed16d5a6` |
| Atomic unit | `u-6f934ca27f23721f58039d9102c868d79b1a7c96` |
| Chunk | `c-5c7b5d56c39cd9c27c1a523edbc76c9a055d99ee` |
| SQL concept document | `19879352-83f7-525a-89d3-b86c3e562cff` |
| SQL chunk | `06b866ca-2d24-5422-a1c4-4967b11cd8c8` |
| Continuation selector | Part 2/25; canonical characters [555, 1107) |
| Strategy/path | `code-lexical`; `source code statement` |
| Chunks / largest enriched chunk | 25 / 779 UTF-8 bytes |
| Sentinel queries | `TRACEJSBEGIN99821` PASS, `TRACEJSMIDDLE99821` PASS, `TRACEJSEND99821` PASS |

### XML

| Stage | Actual identity/evidence |
| --- | --- |
| Source | `367b9a96-da55-46a6-a68e-76d848031d14` |
| Canonical document | `d-8541d58a53fd6e6b08544b3b594aa6e7` |
| Concept | `concepts/manual-trace-xml-fb9f6493592c` |
| Atomic unit | `u-91c5a4c2a01cf0d3c4779b40f5bebaf29a6224d5` |
| Chunk | `c-6dbadd3135aeab38e59488429f1490f12061be74` |
| SQL concept document | `925f735d-0a8d-57c5-a656-8f80ce98c752` |
| SQL chunk | `699c0d84-4155-5665-94a7-c780f98be2b4` |
| Continuation selector | Part 2/25; canonical characters [561, 1108) |
| Strategy/path | `xml-sax-lexical`; `/article[1]/p[1]/text()` |
| Chunks / largest enriched chunk | 25 / 809 UTF-8 bytes |
| Sentinel queries | `TRACEXMLBEGIN99821` PASS, `TRACEXMLMIDDLE99821` PASS, `TRACEXMLEND99821` PASS |

### CSV

| Stage | Actual identity/evidence |
| --- | --- |
| Source | `e8b3cd0e-25cf-47a2-9a27-87dbd93b24c4` |
| Canonical document | `d-02f22b280cee9439975290d4dd853524` |
| Concept | `concepts/manual-trace-csv-40afa3ea4fe5` |
| Atomic unit | `u-00ffc496889552935f3a4f1d5cec5910eb82f00e` |
| Chunk | `c-4596a0158161cba045a0cbf434d8f16bca6a64a4` |
| SQL concept document | `b94c3052-9a86-5bcb-99c3-1e9fe10aac4e` |
| SQL chunk | `95ecc623-be4d-5a95-b272-a13e2d76d07f` |
| Continuation selector | Part 2/25; canonical characters [566, 1110) |
| Strategy/path | `table-cell-lexical`; `table/row/1/column/1` |
| Chunks / largest enriched chunk | 25 / 872 UTF-8 bytes |
| Sentinel queries | `TRACECSVBEGIN99821` PASS, `TRACECSVMIDDLE99821` PASS, `TRACECSVEND99821` PASS |

Detailed selectors, full-unit hashes, provenance and metrics remain in the ignored local `.local/hardening-traces.json` artifact. These IDs are disposable verification data, not application sources.

## Validation results

- Full available Python suite before the final numeric-scalar extension: **334 passed, 2 live-OCR skipped**, one existing LangChain deprecation warning.
- Final affected suite after all scalar/engine changes, with disposable PostgreSQL: **238 passed, 2 live-OCR skipped**. Includes parsing, prepared pipeline, database projection, security, sentinel retrieval, OKF and all four connector workflows. The successful full suite was not rerun.
- Frontend: **115 passed** across 13 test files; ESLint, TypeScript and production build passed.
- Playwright: **7 Connected Apps component checks passed** with mocked external/gateway HTTP; this does not verify live Clerk sign-in or provider OAuth.
- Ruff and mypy passed (93 Python files); Bandit passed; Alembic check reports no new upgrade operations and a single 0005 head. Disposable fixture downgrade/upgrade/re-upgrade was exercised.
- The full command had no failures. A prior combined targeted run exited 143 without a report; its database cases were rerun individually and passed. Harness setup errors were corrected; they did not modify application data.

Run the experiments again:

```bash
myenev/bin/python -m scripts.benchmark_difficult_content --output .local/difficult-content.json
TEST_DATABASE_URL=<disposable_database_ending_test> myenev/bin/python -m pytest backend/tests/test_difficult_content.py
```

Full local metrics are in .local/difficult-content.json; the repository matrix above provides a stable summary. Latencies are measurements, not optimization targets. A 588,000-byte Unicode prose stress probe produced 800 validated chunks with exact reconstruction in 3.38 seconds locally.

## Remaining limitations and exact reasons

- **Live OCR:** Tesseract is not installed in this native environment. The repository adapter remains available when configured with Tesseract/Poppler/language data; no OCR stack or model was added. Scanned table structure/equations and figure-caption relationships remain uncertified.
- **Uncertain PDFs:** rotated/gutter-spanning fixtures are intentionally rejected with review required because existing geometry cannot establish safe order/completeness. All bytes remain available for a stronger configured parser or operator reprocessing.
- **Other code grammars:** conservative lexical splitting cannot certify compilation, regex/template semantics or every language-specific construct. Continuation metadata explicitly avoids that claim; exact original code is preserved.
- **Operational bounds:** the existing upload/page/node/block/chunk/time/memory/output limits remain security/resource constraints. Accepted tested atoms are no longer rejected for the normal chunk budget; exceeding an operational bound still retains the raw source and leaves processing incomplete.
- **Extreme nesting:** 100-level JSON/YAML/XML is verified. Decoder recursion and existing node/process limits still bound arbitrary deeper input; untested nesting is not advertised as certified.
- **Prior artifacts:** old v2 normal artifacts remain compatible; reprocess affected sources to obtain the new engine proof. Legacy v1/JSONL lacks this handoff and requires explicit reprocessing.

**READY FOR EMBEDDING RESEARCH: YES**, for freshly validated sources inside supported operational bounds. Uncertain/unconfigured inputs remain excluded. The next stage can use prepared_source and its complete units/continuation groups without repairing source ownership, offsets, provenance or reconstruction. No embedding/vector functionality was added.
