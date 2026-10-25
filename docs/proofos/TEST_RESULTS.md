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

## Initial delivery verification (before security remediation)

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

The complete evaluator was extended with the ProofOS browser gate. Browser total: 46 checks across component and real-stack boundaries. Remote CI results are recorded in PROGRESS.md. Both dependency audit failures were release blockers at that delivery point; the PR #1 security follow-up below records their remediation. No gate was disabled. No production application migration/deployment, live sign-in/provider consent, paid model request or customer communication is implied.

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

## PR #1 security-check follow-up

Completed remote run [40173154053](https://github.com/MugheesHiader476/Fixflow-/actions/runs/40173154053) on 812f6f4 passed every functional/static/build/migration/browser evaluator gate: 695 backend tests, two OCR skips and 202 frontend tests. Only the two inherited dependency audits failed, so Sonar scan was skipped. The PR follow-up reproduces those failures locally before fixing package versions; original results above remain historical.

| New follow-up validation | Executed outcome |
| --- | --- |
| npm audit --omit=dev --json, before remediation | FAIL: 1 critical dependency / 3 Next advisories |
| Locked Python audit, before remediation | FAIL: 52 advisories / 4 packages |
| npm ci and Python --require-hashes --only-binary=:all: install | PASS using regenerated locks |
| myenev/bin/python -m pip check | PASS |
| Locked Python audit, after remediation | PASS: no known vulnerabilities |
| Production and complete npm audits, after remediation | PASS: zero reported vulnerabilities, including development dependencies |
| PDF package, canonical pipeline and fictional demo regressions, --no-cov | PASS: 57 passed, 2 OCR prerequisite skips, 3.30 seconds |

The complete local evaluator finished successfully against the new separate disposable proofos_pr1_test database: all 19 gates PASS, zero warnings/failures. Command: TEST_DATABASE_URL=disposable FIXFLOW_BROWSER_TOOLS=existing-external-tools FIXFLOW_CHROME=/usr/bin/google-chrome myenev/bin/python scripts/evaluate.py --output .local/pr1-evaluation. Evidence remains ignored at .local/pr1-evaluation/report.html and summary.json.

- Backend: 695 passed, two OCR prerequisite skips, 206.94 seconds, 82% combined coverage; Python 3.14.4 with the hash-locked package set. CI separately runs Python 3.12.
- Frontend: 202 passed / 20 files; 82.14% line coverage with patched Vitest. ESLint/typecheck and Next 16.4.3 production build pass.
- Ruff 0.16.9, mypy (161 files), Bandit, Alembic schema comparison and production client-secret scan pass. Empty migration cycle/populated downgrade refusal remain exercised by the backend suite.
- All five component browser scripts and real full-stack harness pass: 30 component checks plus 16 real Next/FastAPI/PostgreSQL/worker checks, with external identity/provider boundaries simulated.
- Locked Python, production npm and complete npm dependency audits all pass with no known vulnerabilities. No audit ignore, changed gate, suppressed coverage or skipped workflow is introduced.

Pushed-head GitHub Actions and Sonar outcomes are recorded in the PR after dispatch; successful local evaluation is not a remote success claim. No application database migration, deployment, live external consent or customer action is implied.
