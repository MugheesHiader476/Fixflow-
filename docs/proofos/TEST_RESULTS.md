# Executed validation

## Baseline at 965a53a

| Command | Outcome |
| --- | --- |
| npm run lint | PASS |
| npm run typecheck | PASS |
| npm test -- --maxWorkers=1 | PASS, 184 tests / 19 files |
| npm run build | PASS, Next 16.3.8 |
| myenev/bin/python -m ruff check backend scripts | PASS |
| myenev/bin/python -m mypy backend scripts | PASS, 138 files |
| myenev/bin/python -m pytest (disposable proofos_baseline_test) | PASS: 552 passed, 2 OCR prerequisite skips; 158.83 seconds, 81% combined coverage |
| myenev/bin/python -m bandit -r backend scripts -x backend/tests,scripts/tests -q | PASS |
| myenev/bin/alembic check (disposable database) | PASS: no new upgrade operations |
| myenev/bin/python -m pip_audit -r requirements-dev.lock.txt | FAIL before changes: 52 vulnerabilities / 4 packages |

No live Clerk/OAuth/provider/model calls, external writes or application database migrations have been executed. Later results must be recorded with actual counts and limitations.

## Final local verification

| Actual command/boundary | Executed outcome |
| --- | --- |
| npm run lint | PASS |
| npm run typecheck | PASS |
| npm run test:coverage -- --maxWorkers=1 | PASS: 202 tests / 20 files, 34.08 seconds; 82.01% line coverage |
| npm run build | PASS: Next 16.3.8 including Projects, dynamic project workspace, Approvals and icon |
| myenev/bin/python -m ruff check backend scripts | PASS |
| myenev/bin/python -m mypy backend scripts | PASS: 161 source files |
| TEST_DATABASE_URL=disposable myenev/bin/python -m pytest --junitxml=.local/proofos-final-junit.xml | PASS: 695 passed, 2 OCR skips, 212.69 seconds; 82% combined coverage |
| myenev/bin/alembic check (proofos_domain_test) | PASS: no new upgrade operations; full suite exercised empty migration cycle and populated 0011 downgrade refusal |
| myenev/bin/python -m bandit -r backend scripts -x backend/tests,scripts/tests -q | PASS |
| myenev/bin/python scripts/audit_client.py --output .local/proofos-final-client-scan.json | PASS: production browser assets scanned without exposing secrets |
| myenev/bin/python -m pip_audit -r requirements-dev.lock.txt | FAIL: same baseline 52 vulnerabilities / 4 packages; audit not disabled |
| npm audit --omit=dev --json | FAIL: one critical dependency (Next 16.3.8), three advisory entries; dependency versions are unchanged by this branch |
| Existing Playwright component harnesses plus verify-proofos.mjs | PASS: 30 checks across five scripts; actual React/Chromium, simulated HTTP/Clerk, 320/390/1280+ layouts |
| node tests/browser/run-fullstack.mjs (separate proofos_browser_test, isolated Next build) | PASS: 16 checks with real Next gateway, FastAPI, PostgreSQL and ingestion worker; only external identity/providers simulated; no uncaught page errors |
| myenev/bin/python -m scripts.proofos_demo | PASS: deterministic fictional A/B/C JSON/PDF/readiness outputs; four-page PDFs inspected with Poppler |
| git diff --check | PASS |

Backend increase: 552 → 695 passed; frontend: 184 → 202 passed. The same two native OCR tests skip for missing adapters. Early iterations caught fixture drift, selection/policy/verification tampering and decision freshness defects; those were corrected and the final full suite rerun. Logs/JUnit/coverage/browser evidence remain ignored under .local and coverage. PostgreSQL test fixtures used separate *_test databases only, with mocked provider/model HTTP.

The complete evaluator was extended with the ProofOS browser gate. Browser total: 46 checks across component and real-stack boundaries. Remote CI results are recorded in PROGRESS.md. Both dependency audit failures remain release blockers; no gate was disabled. No production application migration/deployment, live sign-in/provider consent, paid model request or customer communication is implied.

## Clean-environment CI follow-up

GitHub Actions run 40169231185 on 1abf2d2 completed with FAILURE. Its evaluation artifact confirms frontend lint/coverage, mypy, Bandit, migration comparison, production build, client-secret scan, all five component browser scripts and the real full-stack harness passed. SonarQube Scan was skipped after the evaluator failed; no Sonar result is claimed.

Besides the inherited dependency audit failures, this run exposed three portability defects: typecheck used generated LayoutProps before Next generated them on a clean checkout; pinned Ruff rejected nested async context managers in the existing Grok transport; and ProofOS connector fixtures relied on local provider configuration. The follow-up generates route types before tsc, combines equivalent bounded async contexts, and makes the shared connector fixture override all synthetic IDs/credentials/origin explicitly. No real provider or .env setting is changed.

| Follow-up validation | Executed outcome |
| --- | --- |
| npm run typecheck (next typegen && tsc --noEmit) | PASS: route types generated before TypeScript |
| myenev/bin/python -m ruff check backend scripts | PASS locally with Ruff 0.16.7; remote uses pinned 0.16.9 |
| myenev/bin/python -m mypy backend scripts | PASS: 161 source files |
| ProofOS, agent and connector pytest modules, --no-cov, disposable proofos_final_test with provider settings explicitly blank in the command environment | PASS: 92 tests, 165.70 seconds |

The 92-test portability run is finished and is not rerun for the user follow-up. Empty command-level settings were test-only overrides, not new application configuration. The previous full 695-test run precedes these CI portability changes; newest remote results must be checked separately.
