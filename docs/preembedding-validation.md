# FixFlow — final pre-embedding system validation

Verified 2026-10-06 12:26 UTC. **READY FOR EMBEDDING RESEARCH: YES**, for authorized sources accepted by the validated contract-v2 handoff. This is a defined test matrix, not a claim about every possible document or a production deployment certification. No embeddings were generated.

## 1. PRE-TEST VERIFIED ARCHITECTURE

Actual browser file/paste form (`src/app/sources/page.tsx`) → same-origin Next route → `src/lib/server/backend-proxy.ts` authenticates external Clerk identity and supplies trusted gateway credentials → `backend/api/routes.py` / `services/uploads.py` registers owner/private raw path/hash → `services/ingestion.py` durable worker/advisory lock/spawned processing → `pipeline/inspection.py` format routing → `parsers.py` → `canonical.py` → `concepts.py` OKF-style structural concepts → `units.py` ordered atomic evidence → `chunks.py` / `splitting.py` structure/content-specific splitting and continuations → `validation.py` independent loss/auth/provenance/reconstruction gates → `repositories/pipeline.py` one PostgreSQL transaction → ready status → `repositories/prepared.py:prepared_source` authorized revalidation of artifact **and actual SQL projections**.

Before this task, difficult-content engine 2.1.1 / contract 2 already existed. Current engine is **2.1.2**, native parser version **5**. The strategy and database architecture were preserved. Legacy JSONL remains a separate manual import path, never silently accepted as v2.

## 2. ENVIRONMENT USED

- Branch: `main`; baseline commit: `6bc5d625efbcf03c09d277584f1ebcc01d7b4f1b`. Working-tree changes listed below.
- Next.js installed/package version 16.3.5; React 19.2.8; Python 3.14.4 in existing `myenev`.
- PostgreSQL 18 local Unix-socket cluster, port 55432. Browser DB: `fixflow_preembedding_test`; pytest DB: `fixflow_pipeline_test`. Both explicitly disposable; no application data used.
- Real Next dev server 127.0.0.1:3012; real Uvicorn 127.0.0.1:8012; external Playwright + installed Chrome. Production build verified separately.
- **Real:** frontend pages, Next routing/proxy, gateway, FastAPI, PostgreSQL/migrations, persistent worker, process boundary, all parsers/chunking/validation/projections/prepared reads.
- **External boundaries simulated:** Clerk SDK identity/widgets, only through exact webpack aliases in an ignored copied application; native connector provider HTTP in backend tests; deterministic OCR-output adapters in downstream tests. Production auth code/config were not bypassed or changed.
- Native Tesseract absent; Poppler available. OCR disabled in this browser environment. No transcription adapter or embedding endpoint/model/dimension configured for this verification.

## 3. PLAYWRIGHT E2E COVERAGE

**118 checks passed**, including **101 source fixtures**. These are real application/network/SQL checks, not a Vite component mock. Each accepted fixture was uploaded from the actual file control, observed via the actual gateway until terminal status, matched to its UI card and inspected through PostgreSQL plus `prepared_source`.

| Real application check | Result |
| --- | --- |
| Real Next signed-out gateway/page and forged identity rejected | PASS |
| Authenticated actual Next page loads without hydration errors | PASS |
| Unbroken long filename remains usable at 390px and 320px | PASS |
| Paste submits once by keyboard and persists a Unicode source | PASS |
| Refresh during processing, interrupted worker and restart retain one source | PASS |
| Backend outage surfaces an error; retry submits the retained form once | PASS |
| Repeated real backend polling failures stop; Refresh resumes after recovery | PASS |
| Real gateway overwrites forged owner; other identity has no private sources/search | PASS |
| Beginning/middle/end keyword sentinels resolve the persisted source | PASS |
| Identical browser upload reuses source and deterministic chunk projection | PASS |
| Owner-scoped identical content keeps distinct source provenance | PASS |
| Changed source version preserves source ID and replaces validated projection | PASS |
| Simultaneous real browser uploads finish without duplicate submissions | PASS |
| Malformed IDs, SQL-like queries and cross-origin writes fail safely | PASS |
| Reload and mobile widths restore terminal sources without overflow | PASS |
| Terminal sources stop source-status polling | PASS |
| PostgreSQL has exact source inventory, valid FKs and no embeddings/orphans | PASS |

Runtime/page errors: **0**. Unexpected console/hydration errors: **0**. Intentional rejected requests and stopped-backend requests produced 7 recorded HTTP console messages; they remain in the JSON evidence rather than being presented as clean successful requests.

## 4. SOURCE TYPES TESTED

82 expected-ready fixtures, 16 expected processing failures, 3 expected API/UI rejections: **101/101 expected outcomes passed**. Every extension accepted by the current router is represented: .c, .cpp, .cs, .csv, .docx, .eml, .go, .h, .htm, .html, .java, .jpeg, .jpg, .js, .json, .jsx, .log, .md, .mp3, .mp4, .pdf, .php, .png, .pptx, .py, .rb, .rs, .rst, .sh, .sql, .srt, .tif, .tiff, .ts, .tsx, .txt, .vtt, .wav, .webm, .xlsx, .xml, .yaml, .yml. `.exe` was separately rejected. Audio/video fixtures test signatures and missing-adapter behavior; they do not verify media decoding/transcription. Supplementary OCR-output and legacy rows are explicitly non-browser checks.

| Source type | Fixture | Upload/API | Parser | Canonical | Chunking | Coverage | Reconstruction | Persistence | prepared_source | Playwright | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| text | normal-short.txt | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-headings.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| text | normal-long.txt | 202 → ready_for_embedding | native | PASS | sentence | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-nested.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-list.md | 202 → ready_for_embedding | native | PASS | element, list_item | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | normal-sample.py | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | normal-large.py | 202 → ready_for_embedding | native | PASS | element, statement | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| json | normal-simple.json | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| json | normal-nested.json | 202 → ready_for_embedding | native | PASS | element, subtree | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| yaml | normal-data.yaml | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-table.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-large-table.md | 202 → ready_for_embedding | native | PASS | element, row_group | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-repeated.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| text | normal-unicode.txt | 202 → ready_for_embedding | native | PASS | sentence | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-tiny.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-huge.md | 202 → ready_for_embedding | native | PASS | element, prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| markdown | normal-concepts.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | normal-quote.md | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| json | normal-empty-object.json | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| xml | normal-mixed.xml | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| text | hard-sentence.txt | 202 → ready_for_embedding | native | PASS | prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| text | hard-clauses.txt | 202 → ready_for_embedding | native | PASS | prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| markdown | hard-paragraph.md | 202 → ready_for_embedding | native | PASS | element, prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-function.py | 202 → ready_for_embedding | native | PASS | element, statement | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | hard-statement.py | 202 → ready_for_embedding | native | PASS | element, python-ast-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-function.js | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-object.js | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-class.ts | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-class.java | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-function.c | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-function.cpp | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-function.go | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-function.rs | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| json | hard-nested.json | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| json | hard-scalar.json | 202 → ready_for_embedding | native | PASS | element, structured-path-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| json | hard-integer.json | 202 → ready_for_embedding | native | PASS | structured-path-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| json | hard-exponent.json | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| json | hard-precise.json | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| yaml | hard-nested.yaml | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| yaml | hard-scalar.yaml | 202 → ready_for_embedding | native | PASS | element, structured-path-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| yaml | hard-integer.yaml | 202 → ready_for_embedding | native | PASS | structured-path-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| yaml | hard-exponent.yaml | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| xml | hard-nested.xml | 202 → ready_for_embedding | native | PASS | xml-sax-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| xml | hard-text.xml | 202 → ready_for_embedding | native | PASS | xml-sax-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| markdown | hard-table.md | 202 → ready_for_embedding | native | PASS | row_group | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| markdown | hard-cell.md | 202 → ready_for_embedding | native | PASS | table-cell-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| csv | hard-row.csv | 202 → ready_for_embedding | native | PASS | table-cell-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| csv | hard-header.csv | 202 → ready_for_embedding | native | PASS | table-cell-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| text | hard-unicode.txt | 202 → ready_for_embedding | native | PASS | prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-minified.js | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-query.sql | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-call.js | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | hard-chain.js | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| code | component.jsx | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | component.tsx | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | header.h | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | class.cs | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | script.sh | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | script.rb | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | script.php | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| text | reference.rst | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| html | page.htm | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| yaml | mapping.yml | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| transcript | speech.srt | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| text | sentinels.txt | 202 → ready_for_embedding | native | PASS | prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| text | stress.txt | 202 → ready_for_embedding | native | PASS | prose-clause-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| markdown | stress.md | 202 → ready_for_embedding | native | PASS | element, sentence | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| code | stress.js | 202 → ready_for_embedding | native | PASS | code-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| json | stress.json | 202 → ready_for_embedding | native | PASS | element, subtree | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| xml | stress.xml | 202 → ready_for_embedding | native | PASS | xml-sax-lexical | 100% | PASS | PASS | ACCEPT | PASS | PASS |
| csv | stress.csv | 202 → ready_for_embedding | native | PASS | row_group | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| docx | native-guide.docx | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| pptx | native-slides.pptx | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| xlsx | native-workbook.xlsx | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| html | native-page.html | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| email | native-message.eml | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| log | native-events.log | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| transcript | native-speech.vtt | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| pdf | native-digital.pdf | 202 → ready_for_embedding | native | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| pdf | native-table.pdf | 202 → ready_for_embedding | layout | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| pdf | native-columns.pdf | 202 → ready_for_embedding | layout | PASS | element | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| pdf | native-integrity-audit.pdf | 202 → ready_for_embedding | layout | PASS | element, sentence | 100% | N/A | PASS | ACCEPT | PASS | PASS |
| pdf | rejected-scanned.pdf | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| image | rejected-scan.png | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| pdf | rejected-difficult-layout.pdf | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| pdf | rejected-rotated-layout.pdf | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| image | unconfigured.jpg | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| image | unconfigured.jpeg | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| image | unconfigured.tif | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| image | unconfigured.tiff | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| json | invalid.json | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| yaml | invalid.yaml | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| xml | invalid.xml | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| pdf | invalid.pdf | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| text | zero.txt | 422 (expected) | N/A | N/A | N/A | N/A | N/A | no source created | N/A | PASS | PASS: safe rejection |
| unsupported | binary.exe | 415 (expected) | N/A | N/A | N/A | N/A | N/A | no source created | N/A | PASS | PASS: safe rejection |
| text | invalid-utf8.txt | 422 (expected) | N/A | N/A | N/A | N/A | N/A | no source created | N/A | PASS | PASS: safe rejection |
| audio | unconfigured.wav | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| audio | unconfigured.mp3 | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| video | unconfigured.mp4 | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| video | unconfigured.webm | 202 → failed (expected) | explicit failure/review | NOT PROMOTED | N/A | N/A | N/A | raw retained; no projections | EXCLUDE | PASS | PASS: safe rejection |
| OCR adapter output | oversized prose/code/JSON/YAML/XML/table outputs | test adapter; no browser upload | CallbackParser | PASS | content-routed continuation | 100% | PASS | downstream contract test | validated contract | N/A | PASS; live OCR unverified |
| legacy v1 | damaged v1 artifact / legacy import and upgrade | backend/SQL tests | legacy importer; explicit reprocess | v2 only after reprocess | legacy 3000 chars/400 overlap | v2 validation after upgrade | N/A | PASS | REJECT until v2 | N/A | PASS |

## 5. PARSER RESULTS

Persisted parser selections across ready browser fixtures: `{'native': 79, 'layout': 3}`. Routing is recorded per fixture in the JSON report, including actual atomic boundary/continuation strategies. Native Python AST and conservative lexical/declaration code handling were tested for Python, JavaScript, TypeScript, JSX, TSX, Java, C, C++, Go, Rust, C#, shell, SQL, Ruby and PHP. Only Python claims AST-level analysis; other tested languages have conservative lexical/symbol evidence and reconstructable continuation fallback, not full grammar certification.

PDF: digital text, two-column order, table geometry and two-page sentinel document passed. Rotated/gutter-spanning layouts failed quality gates with raw source retained. DOCX/PPTX/XLSX table/location regressions passed. HTML removes script/style/non-visible comments deterministically. Email preferred body + subject remained searchable; original ordered/duplicate MIME headers now survive as explicitly non-retrieval provenance metadata.

## 6. CHUNKING RESULTS

Concept-first, then child/direct structural units, paragraphs/sentences/statements/rows/subtrees, then content-specific lexical continuations. Default maximum **1024 UTF-8 bytes including retrieval context**, preferred minimum **16**, zero sliding content overlap. Table schemas/heading context may repeat intentionally. `token_count` is byte budgeting in this configuration, not embedding-model tokens.

Across the 82 ready matrix fixtures: 2102 persisted chunks; minimum retrieval size 22 bytes; maximum 1024 bytes. Small complete units can be below the preferred minimum. The complete corpus checks deterministic boundaries/order/hashes, reciprocal neighbors, concept/container links, symbol/path/row provenance, repeated evidence at distinct positions and independently damaged metadata.

## 7. CONTINUATION RESULTS

31 continuation groups / 1553 continuation fragments in the ready browser matrix. All exact reconstructions passed, including giant prose, code expressions, JSON/YAML scalars, XML and table cells/rows/headers. Persisted indices are **0..N-1**; rendered `Part` labels are **1..N**. Counts, exact character offsets, full-unit hashes, group identity, source authorization, paths and previous/next links are independently validated. A continuation is a fragment of a complete resolvable atomic unit; it is never falsely labeled standalone JSON/XML/code syntax.

## 8. CANONICAL→CHUNK COVERAGE RESULTS

Every ready fixture: **100% retrieval-relevant canonical coverage; uncovered elements = 0; duplicate content positions = 0**. Character selectors, JSON-pointer leaves/array ranges, table row ranges and contextual headings are checked independently of the splitter. Every continuation reconstructs exactly. Repeated authored text is retained at its distinct positions. Intentional contextual/schema repetition is not counted as duplicated content positions. These are measured results for this corpus, not universal parser guarantees.

## 9. ORIGINAL→CANONICAL SANITY RESULTS

Measured separately from chunk coverage: raw SHA-256/bytes retained for all accepted sources; text/code non-whitespace character equality (Unicode included); source-derived first/middle/last terms for suitable normal fixtures; JSON/YAML tree/path/value/container regressions; exact large XML/raw lexical numeric reconstruction; table cell/schema expectations; Office location expectations; complete ordered email headers.

Four successful PDF fixtures had **100% original native-extracted-word recall**. The known two-column fixture also has an exact four-paragraph ordering assertion and bounding boxes. This does not certify figure pixels, arbitrary PDF reading order or every document layout. HTML script/style and format declarations, Markdown delimiters, CSV quoting/JSON formatting, transcript timestamps and MIME routing headers are not all retrieval body text; structure/provenance or raw source retains their applicable information. MIME alternative bodies/attachments are not separately projected by native EML ingestion.

## 10. AUTHORIZATION RESULTS

Signed-out protected page redirects; Next private API and direct unauthenticated backend return 401. Two simulated external identities traverse the unchanged real gateway: B sees no A source in source-ID API, list or keyword search; forged browser identity/authorization headers cannot override B. Direct prepared reads for a foreign owner return `None` before private artifact decoding. Connector availability/connection/ACL-lease exclusion remains covered by real-SQL backend regressions with mocked external provider HTTP. Live Clerk login, provider consent and live provider ACL delivery remain unverified, explicitly separated from application owner enforcement.

## 11. PROVENANCE RESULTS

Actual persisted examples were inspected end to end (complete slices/hashes/locations/SQL FKs in `.local/preembedding/manual-traces.json`):

| Fixture | Source UUID | Canonical document | Concept | Atomic unit | Chunk ID | SQL chunk UUID |
| --- | --- | --- | --- | --- | --- | --- |
| normal-headings.md | f1eb7b35-9dd7-4556-9e8c-cea79ec17df6 | d-5d5179a5ec92931f200775224923ac7d | concepts/guide-9f2432b81d5f | u-236431c046ed22736c8ed6a49197e11adf3ccbd9 | c-d52fd0ea5c8ae5da3ccb789d8dd641c2444e027a | 0d231780-d05e-5719-ae68-537ff388de55 |
| hard-sentence.txt | ac21c996-45a5-4ae4-b717-e2d099271a34 | d-7e643a42ba99d026fc3e774bf4a24506 | concepts/hard-sentence-txt-95177ce9295d | u-5ac8e3afa27d228cade8463d4238ebb597573ae1 | c-c5e7638902f9ae8ce9094ab0a3de81900bb94a52 | 66b640ce-e27a-594e-8a62-6086c85d76ba |
| normal-large-table.md | 1c887de2-0eee-4526-b6ba-decc8e347b86 | d-3935aeab8964c6a0126c131345009fff | concepts/metrics-61a9772e1c1f | u-a2bcf2e00a2b303a105f20b2d4d8653ac6652e4b | c-bc57a2eb66af0c54fd3e9084c4a13d5d614151bc | 028549b2-7d98-5008-ad01-eb4e20642a60 |
| native-columns.pdf | 6e723600-9d81-45c7-95ec-3eecb3a8a015 | d-d32026e8b301c93f1a7fc2a7a158aa41 | concepts/native-columns-pdf-43bc1334c30d | u-0a5f9cbe13d35d081aa04447c0e5600bd1b8fbde | c-7340207cbcd43936f6d9eaf7016eb78e5219833c | 1e692ff4-1465-5285-89f5-0101742ec5f5 |
| native-integrity-audit.pdf | d05bc653-8f5a-47e1-947d-4bbc7f4efaa7 | d-1a95c449e67a3813ba7e021fb973b969 | concepts/native-integrity-audit-pdf-538298304627 | u-ac0ef55689564051ee9afcbc5ec367cfabb8a488 | c-483b99fccacb988c19f0e0836a8418a9b621f5be | 480252d1-2d96-547a-91d8-3dd4b1670208 |

The Markdown definition preserves its source line; the long sentence preserves one full-unit group with exact offsets and Unicode reconstruction; the table preserves schema and one-based row range. SQL document UUIDs are concept projections; canonical document IDs refer to the source-wide artifact. Canonical character offsets are not automatically original-file byte offsets. Page/bbox, line, sheet/cell, slide and timestamp locations remain nullable where extraction cannot establish them.

## 12. HASH/VERSION/IDEMPOTENCY RESULTS

Identical browser re-upload: same source ID/hash/version/chunk projection, no uncontrolled duplicate records. Manual same-owner equal bytes reuse the first source identity/name even if a subsequent filename differs; connector external identities and different owners remain distinct. Same-source changed content: stable source ID, version increases by one and changed projection replaces transactionally. Equal content in two owners: distinct source identities/provenance/authorization. Connector external identities remain distinct even for equal bytes (backend regression). Unchanged forced processing is deterministic. Failed updates retain the previous valid artifact/projection; retry recovery is verified in existing PostgreSQL tests.

Engine/parser version bumps invalidate processing caches on reprocessing. Already-validated old v2 email artifacts are not mass-migrated by this task; explicit force/update reprocessing is required to add the newly retained headers. Legacy v1/JSONL cannot pass the v2 handoff without original-source reprocessing.

## 13. DATABASE INTEGRITY RESULTS

Final browser DB: **103 sources, 219 concept documents, 3806 chunks**. Status counts: `{'ready_for_embedding': 89, 'failed': 14}`. Exact expected source-ID inventory matched. Orphans/cross-source FKs: **0**. Generated embeddings: **0**. Pending/stuck sources: **0**. Failed first ingestions retained raw bytes and exposed no document/chunk projections. Composite FK, migration preservation, transaction rollback, deduplication and tampering regressions passed in the disposable pytest DB.

## 14. PREPARED_SOURCE RESULTS

Every ready browser fixture was accepted by `prepared_source(session, source_id, trusted_owner_id)` after independent v2 validation plus SQL projection comparison. Every failed source was excluded. New PostgreSQL gate cases positively establish a complete huge-XML handoff, then independently damage continuation count, provenance, authorization, contract version or SQL chunk projection: each invalid handoff raises `ValueError`. Inactive, processing, failed and foreign-owner cases return `None` before decoding deliberately invalid private artifacts. Existing lease/connector, document metadata/content/hash/parent and v1 upgrade tests also pass.

## 15. LOADING/STATUS RESULTS

HTTP 202 means acceptance, not completion. Stored uploaded/processing/terminal states and UI status match; transient `chunked` is inside the final transaction and is not promised as a visible intermediate state. The large source was observed processing, reloaded and subjected to a real worker/server stop/restart; it retained one identity/version and reached ready. A real backend outage displayed a safe error and preserved the form for one retry. Two simultaneous browser uploads completed once each.

Successful polling uses the existing two-second cadence. Three consecutive status-refresh failures now stop automatic retries with a visible Refresh instruction; real stopped-backend Playwright and fake-timer regression prove the cap and recovery. Terminal sources stop polling. A polling outage preserves last known state; it never fabricates pipeline failure/completion.

## 16. RESPONSIVE/UI RESULTS

1440px desktop, 390px and 320px mobile passed. Selected unbroken 243-character filename no longer expands a 320px page to 1655px. Upload/paste/keyboard submit, retained-error retry, refreshed terminal cards and long status/name wrapping passed. No unexpected runtime/hydration/console errors. Drag/drop is **NOT IMPLEMENTED** in this page; file picker and paste are the verified controls.

## 17. PERFORMANCE/STRESS RESULTS

Local dev-server measurements, not production SLAs. Acceptance includes browser action and API response; processing interval below is acceptance→observed terminal and includes queueing/worker/SQL/poll latency. Parser/quality durations were not retained in the full-run server logs because the harness originally checked only exitCode, omitting SIGTERM-stopped server output. The guard was corrected and separately verified without changing the completed corpus database. Full-run per-category parser timings therefore remain explicitly unavailable. The separate short-text log check recorded parser+quality at 0.2 ms; this is one fixture, not an estimate for the full corpus. Chunking/validation duration and peak RSS are not separately instrumented; no fabricated timings are reported.

| Ready category | Fixtures | Median acceptance | Median accepted→terminal | Max total terminal | Median parser+quality | Total chunks | Chunk bytes min–max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| code | 24 | 188 ms | 1.83 s | 4.55 s | not logged | 438 | 95–1023 |
| csv | 3 | 182 ms | 2.24 s | 2.48 s | not logged | 124 | 235–1023 |
| docx | 1 | 246 ms | 2.30 s | 2.55 s | not logged | 2 | 90–96 |
| email | 1 | 388 ms | 2.30 s | 2.68 s | not logged | 1 | 96–96 |
| html | 2 | 205 ms | 2.59 s | 3.17 s | not logged | 3 | 68–96 |
| json | 9 | 182 ms | 1.21 s | 3.63 s | not logged | 151 | 22–1021 |
| log | 1 | 165 ms | 1.31 s | 1.47 s | not logged | 2 | 93–117 |
| markdown | 14 | 173 ms | 1.87 s | 5.27 s | not logged | 328 | 25–1024 |
| pdf | 4 | 158 ms | 1.68 s | 2.47 s | not logged | 16 | 64–1006 |
| pptx | 1 | 213 ms | 2.93 s | 3.14 s | not logged | 1 | 76–76 |
| text | 9 | 190 ms | 1.38 s | 4.98 s | not logged | 836 | 63–1024 |
| transcript | 2 | 191 ms | 1.17 s | 1.37 s | not logged | 3 | 61–78 |
| xlsx | 1 | 207 ms | 2.93 s | 3.14 s | not logged | 1 | 82–82 |
| xml | 4 | 173 ms | 1.46 s | 2.27 s | not logged | 148 | 97–808 |
| yaml | 6 | 194 ms | 1.46 s | 2.55 s | not logged | 48 | 22–793 |

| Stress fixture | Source bytes | Chunks | Chunk bytes min/median/max | Acceptance | Total terminal | Coverage |
| --- | --- | --- | --- | --- | --- | --- |
| stress.txt | 532025 | 725 | 170/781/781 | 190 ms | 4.98 s | 100% |
| stress.md | 164058 | 240 | 426/724.5/1023 | 172 ms | 5.27 s | 100% |
| stress.js | 195822 | 271 | 360/769/769 | 179 ms | 3.71 s | 100% |
| stress.json | 67993 | 97 | 836/1010/1021 | 163 ms | 3.50 s | 100% |
| stress.xml | 75799 | 105 | 152/804/807 | 187 ms | 2.27 s | 100% |
| stress.csv | 87788 | 113 | 541/1009/1023 | 238 ms | 2.48 s | 100% |
| native-integrity-audit.pdf | 26924 | 13 | 64/142/1006 | 154 ms | 1.28 s | 100% |

All stress fixtures terminated, retained complete coverage and remained within existing operational limits. There is no extrapolation to maximum-50-MiB throughput or every deeply nested tree. Explicit resource-bound failures remain appropriate and retain the raw source.

## 18. NEGATIVE TEST RESULTS

Empty/invalid UTF-8: 422; unsupported binary: 415, also rejected in the file UI. Malformed JSON/YAML/XML/PDF: accepted raw source then safe failed processing with no false ready/prepared output. Scanned/image input is identified and fails explicitly with OCR unavailable; uncertain layouts are review failures. Missing transcription adapter fails explicitly. Malformed IDs, harmless SQL-like search and cross-origin writes are handled safely. Process deadlines/output bounds, unsafe XML entities, YAML cycles/tags/duplicate keys and non-finite JSON/YAML regressions passed.

## 19. KEYWORD RETRIEVAL REGRESSION

Browser gateway beginning/middle/end unique queries returned the expected authorized sentinel source. Backend PostgreSQL sentinel tests also cover boundary terms, oversized continuation records, three-format traces and owner exclusion. Keyword `simple` full-text OR query / GIN / rank behavior remains intact. No vector search, hybrid search, reranking or LLM answer generation was added.

## 20. BUGS FOUND AND FIXED

| Bug | Reproduction | Root cause | Fix | Regression test | Final result |
| --- | --- | --- | --- | --- | --- |
| Mobile filename overflow | 320px viewport widened to 1655px after selecting an unbroken filename | grid intrinsic minimum + unbreakable selected name | min-w-0 panels + break-all filename | real Next Playwright 390/320 regression; before/after screenshots | PASS |
| EML provenance header loss | ordered/duplicate From/To/Date/X-Reference headers missing from canonical metadata | native email parser retained only message/thread IDs | retain original ordered headers as classified non-retrieval metadata; parser5/engine2.1.2 | test_uploaded_email_preserves_ordered_headers_as_nonretrieval_metadata + native EML browser/prepared check | PASS |
| Unbounded status polling on outage | 7 calls over 12s rather than initial call + 3 failed polls | finally scheduled another poll without failure bound | cap consecutive failures at3; Refresh re-arms; preserve last status | pages fake timers + real stopped backend E2E recovery | PASS |
| Lint scanned raw fixtures/generated Next output | full lint inspected .local uploaded code and webpack output | generated runtime directory absent from global ignores | ignore .local generated data only; app/harness rule assertions retained | tests/frontend/tooling.test.ts; baseline config reproduction; full lint | PASS |
| Server-log capture omitted signal exits | Successful server shutdown produced null exitCode/non-null signalCode; server log files were not refreshed | teardown saved only numeric exit codes | include terminated signal exits when writing captured server output | focused real Next/FastAPI/PostgreSQL rerun on separate fixflow_logging_test DB | PASS: 5 real application checks; backend/Next shutdown logs retained |
| Browser tooling manifest interruption | one trial returned Next-dev 500: Manifest file is empty during concurrent validation | observed dev manifest regeneration; underlying Next race not conclusively established | fresh disposable .next per run, explicit staged cwd, final Next E2E run after build | unchanged strict HTTP assertion and full fresh browser rerun | final clean run PASS; concurrency not certified |

The static-tooling test initially exceeded Vitest's generic five-second timeout while loading real ESLint plugins under parallel checks. It now uses Node environment and a per-tooling-test 15-second budget, retaining every assertion. HTML hidden-script/VTT declaration probe corrections were harness fixes, not hidden product data-loss failures. Failure evidence is retained under `.local/preembedding/manifest-failure/` and the email/poll/tooling before logs.

## 21. FILES CHANGED

- `AGENTS.md`
- `SKILLS.md`
- `docs/ingestion-pipeline.md`
- `docs/preembedding-validation.md`
- `backend/processing/pipeline/parsers.py`
- `backend/processing/pipeline/runner.py`
- `backend/tests/test_preembedding_handoff.py`
- `src/app/sources/page.tsx`
- `tests/frontend/pages.test.tsx`
- `tests/frontend/tooling.test.ts`
- `eslint.config.mjs`
- `tests/browser/clerk-client.tsx`
- `tests/browser/clerk-server.mjs`
- `tests/browser/preembedding.py`
- `tests/browser/run-preembedding.mjs`

No dependency/package/runtime framework, embedding provider, retrieval algorithm or chunking strategy changed. New browser tools exercise the existing application; Clerk simulators are test-only and aliased only in an ignored copied checkout.

## 22. MIGRATIONS CHANGED/ADDED

**None.** Metadata uses existing JSONB. Actual upgrade to head and all migration/integration tests passed; `alembic check`: **No new upgrade operations detected**. Single existing head remains 0005. No `create_all`, stamping, schema/data reset outside the explicitly disposable test databases or production-data migration occurred.

## 23. AGENTS.md UPDATE

Updated after the final clean run: engine/native parser versions, actual continuation strategies/reconstruction requirements, canonical loss validation, authorized prepared boundary, status semantics, preserved raw failures, email header classification, bounded polling, real Next E2E command/environment and truthful external/OCR/legacy/embedding limitations. Existing architecture/security/connector rules were retained.

## 24. SKILL.md UPDATE

The repository has **SKILLS.md**, no relevant existing `SKILL.md`. Updated that existing skills documentation without creating a duplicate. It now documents purpose, source types/router, canonical/concept/atomic/chunk contracts, selectors/continuations, provenance/auth/coverage/prepared usage/statuses, safe parser/splitter extension, required tests, real Playwright command and the exact embedding handoff/limitations.

## 25. FULL FINAL TEST RESULTS

| Gate | Final result |
| --- | --- |
| Backend full pytest with disposable PostgreSQL | 356 passed; 2 live OCR skipped; 1 existing LangChain deprecation warning; 144.03s |
| Frontend full Vitest coverage | 118 passed in 14 files |
| Playwright real Next/FastAPI/PostgreSQL | 118 passed; 101/101 fixture outcomes passed; zero unexpected runtime/hydration errors |
| ESLint / TypeScript / production Next build | PASS |
| Ruff backend/scripts/browser helper | PASS |
| mypy backend/scripts/browser helper | PASS: 95 source files |
| Bandit production backend/scripts | PASS |
| Alembic upgrade, schema check and DB integration | PASS; no new upgrade operations |
| Coverage | Backend total 81%; frontend statements 85.21%, lines 89.03%, branches 79.86% |
| Live Clerk/OAuth/providers/OCR | UNVERIFIED external boundaries; native OCR tests explicitly SKIPPED, not fabricated PASS |

Commands: `npm run lint`; `npm run typecheck`; `npm run test:coverage`; `npm run build`; `myenev/bin/python -m pytest -v --junitxml=.local/preembedding/backend-junit.xml` with disposable `TEST_DATABASE_URL`; `myenev/bin/python -m ruff check backend scripts tests/browser/preembedding.py`; `myenev/bin/python -m mypy backend scripts tests/browser/preembedding.py`; Bandit command from AGENTS; `myenev/bin/alembic check` with disposable `DATABASE_URL`; real browser command in SKILLS/AGENTS.

Additional logging regression evidence: `.local/preembedding-logcheck/browser-report.json` (five checks), `command-3.log` (actual Uvicorn/pipeline/shutdown), `command-4.log` (actual Next). Stale earlier server logs were preserved under `.local/preembedding/prior-server-logs/` and are not final-run timing evidence.

Evidence: `.local/preembedding/browser-report.json`, `browser-trace.zip`, `backend-junit.xml`, `frontend-junit.xml`, `manual-traces.json`, command logs; `.local/preembedding-*.log`; `coverage/python-coverage.xml`, `coverage/frontend/lcov.info`. Screenshots include selected filename320/390, normal ready, hard sentence continuation case, stress source, failed JSON, refreshed processing, bounded-polling outage and final desktop/mobile. Evidence is ignored and contains only safe synthetic test data. Harness-owned servers are stopped at teardown.

## 26. REMAINING VERIFIED LIMITATIONS

| Limitation | Reason | Current behavior | Data-loss risk | Safe? | Blocks embeddings? | Recommended future action |
| --- | --- | --- | --- | --- | --- | --- |
| Live identity/provider consent unverified | external Clerk/OAuth/provider HTTP not exercised | actual gateway/owner/ACL logic tested with external boundary simulation | no loss demonstrated; live integration assumptions remain | local contract verified; no production-consent claim | No for prepared test sources | operator/live account smoke test before production rollout |
| Native OCR unavailable | Tesseract not installed in this native environment | scans/images fail explicitly; downstream OCR-output adapter corpus passes | no silent loss: raw retained, no prepared output | Yes: excluded | Only for those sources | install/configure existing adapter prerequisites; rerun two live OCR tests |
| Audio/video transcription unavailable | no trusted adapter/model configured | explicit safe failure after signature routing | raw retained; no claimed transcript | Yes: excluded | Only for those sources | separate future adapter task if product needs it |
| Rotated/gutter-spanning PDF layouts | current positional parser cannot certify order | layout_uncertain quality failure; raw retained; no ready/prepared | no silent promoted loss; extraction incomplete | Yes: excluded | Only for those sources | review/source conversion or stronger measured adapter |
| Arbitrary untested PDF/Office visuals | defined text/table/layout fixtures do not certify diagrams, complex equations or all reading orders | existing quality gates/assets/warnings; no universal completeness claim | unseen extraction risk cannot be excluded by canonical coverage | Only within verified matrix/quality policy | No for validated matrix; review unseen layouts | expand targeted extraction fixtures before new format claims |
| Non-Python full syntax analysis | conservative lexical/declaration parser, not full grammars | exact reconstructable code fragments with symbols/lines where established | no measured content loss; syntax fragment may require parent expansion | Yes: fragment contract explicit | No | evaluate retrieval quality per language in later research |
| Native EML MIME alternatives/attachments | preferred body parser does not ingest every MIME part separately | subject/preferred body searchable; all headers metadata; original MIME retained | attachment/alternative not separately searchable, explicitly limited | Yes within preferred-body support | No for validated preferred body | separate multipart expansion only if required; connector attachment path remains separate |
| Older validated v2 EML metadata | no destructive bulk reprocessing introduced | previously valid v2 may lack newly retained headers until force/update processing | headers still in raw EML, absent in older canonical metadata | Valid old contract; do not claim new headers | No for new fixtures; reprocess affected old mail before provenance-sensitive embeddings | explicit force/update reprocessing of affected EML |
| Legacy JSONL/v1 | no complete validated v2 artifact/projection | keyword legacy remains; prepared_source raises reprocessing error | no silent acceptance | Yes: excluded | Yes for those rows until reprocessed | reprocess original source and explicit legacy owner assignment |
| Operational limits | bounded untrusted processing is intentional | 50 MiB upload; 5M extracted chars; 1000 pages; 20k blocks; 10k chunks; 180s deadline; 1GiB Linux address space; 128MiB result default | raw retained after accepted-source processing failure | Yes | Only for over-limit sources | measure workload and explicit safe bounds; no unlimited-size claim |
| Concurrent Next development builds | one observed transient empty-manifest trial; framework cause not conclusively established | fresh staged build cache/cwd; final E2E after production build is clean | no source corruption demonstrated | ingestion contract verified; concurrent dev build not certified | No | keep Next build and browser runs sequential; investigate separately if repeated |

## 27. EMBEDDING HANDOFF CONTRACT

Consume **`await prepared_source(session, source_id, trusted_owner_id)`**, returning validated `PipelineResult` or `None` for an inaccessible/not-ready source. Corrupt/incomplete/v1 evidence raises `ValueError`; do not catch and treat it as valid text. Authorization must come from trusted server context; keep existing SQL source-access/lease checks authoritative.

Use `result.chunks` together with `result.canonical`, `concepts`, `atomic_units`, `parents`, `coverage` and `statistics`. Each chunk includes deterministic chunk/concept/source/canonical-document IDs; raw and retrieval text; type; section and structural selectors; parent/neighbor/global order; block/page/line/bbox/sheet/slide/timestamp provenance where known; content hash, byte/character/budget lengths; authorization scope/source context; exact atomic slices and continuation group/index/count/full-unit hash/path/row-column identity. Source version/hash and engine/config/stage hashes are resolvable through the result and persisted metadata. SQL chunk UUID is `uuid5(UUID(source_id), chunk.chunk_id)`; SQL concept-document UUID is `uuid5(UUID(source_id), chunk.concept_id)`. Both are separate from the canonical document ID.

The embedding stage must deliberately choose `retrieval_content` or `raw_content` and bind its model/config hash to that exact validated text. Do not lose selectors, citation identity, owner/ACL scope, continuation metadata or source version. Never accept chunks merely because a source status says ready; use this verified reader. No model selection, generated vectors, vector retrieval, reranking or answer generation was implemented here. Pre-existing optional HTTP embedding/vector modules remain untouched and unconfigured in this verification.

## 28. FINAL DECISION

**READY FOR EMBEDDING RESEARCH: YES.** The defined current matrix and the validated v2 handoff pass. Research can consume newly validated authorized `prepared_source` results directly. Explicitly excluded/unconfigured/legacy sources do not become embedding-ready by implication. This is a substrate handoff decision, not live provider, OCR, unlimited document support or production-auth certification.
