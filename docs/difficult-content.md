# Difficult parsing content

Parser regression fixtures remain in `backend/tests/fixtures/pipeline/` and `backend/tests/difficult_corpus.py`. The regression suite exercises long Unicode prose/code/table cells, large or inexact JSON/YAML numbers, deep paths/container identity, XML mixed text/tails/original syntax, YAML comments, Office/PDF tables, email headers and PDF reading order.

Long source units remain canonical blocks. There is no chunk-size/token limit or continuation/fragment projection. Operational character/block/upload/archive/memory/output/time bounds remain. Numeric fallback retains the original validated serialization rather than coercing huge/inexact numbers or relaxing conversion safety.

`test_pipeline.py`, `test_pipeline_integrity_audit.py` and `test_difficult_content.py` retain parser quality, content, provenance, unsafe-input, cache, process-boundary and persistence checks. `test_parsing_boundary.py` verifies the parsing-only handoff and rejects damaged evidence/authorization. Large output is tested without inference. Model benchmark and prepared-chunk harnesses were removed along with their features.

Detected rotated/gutter-spanning PDF layouts remain `layout_uncertain` with review required; raw files remain available. Native OCR requires installed Tesseract and Poppler. Synthetic OCR adapters test the contract only. Complete canonical validation does not prove arbitrary unseen source extraction quality.

```bash
myenev/bin/python -m pytest backend/tests/test_pipeline.py backend/tests/test_pipeline_integrity_audit.py backend/tests/test_difficult_content.py
myenev/bin/python -m scripts.run_pipeline backend/tests/fixtures/pipeline/guide.md --out .local/parsing-example
```
