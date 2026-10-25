# Known limitations and launch blockers

The core owner project → reviewed contract → frozen milestone → selected evidence → deterministic verification → private package → distinct customer decision → local invoice draft workflow runs in the existing architecture. It is not every requested optional capability.

## Not implemented

- Slack notifications/Drive package uploads: no write scopes, outbox or dispatch jobs; controls unavailable, private downloads supported.
- Organization/team memberships: current owner is internal reviewer; one separately configured customer Clerk subject. Review requests are local app records and send no message.
- Autonomous SRS milestone/task planning, coding runner or GitHub writes: approved engineering packet only; no execution or self-approval.
- Waivers, arbitrary verification plugins, historical-release association, PR-review verdicts, coverage thresholds or direct deployment health probes. Unsupported checks stay unverified; a successful GitHub deployment status is an observation, not an availability SLA.
- Invoice issuance/sending, taxes, payments or bank configuration. Only explicit frozen contractual fields enter a local draft.

## Evidence and format bounds

Requirement proposals recognize bounded English obligation language (must/shall/should/required/requirement/acceptance/will) and preserve canonical spans. Compound prose is not universally decomposed. Every proposal needs human review; uncertain acceptance conditions remain unverified. Existing parser uncertainty/format bounds apply.

GitHub collection requires current selected branch head, exact workflow/run/SHA, current access, safe bounded JUnit archive and matching digest. Existing transport refuses ordinary cross-host artifact redirects; those reports stay unavailable. Rerun manifests do not prove attempt attribution and remain unverified. A green workflow/job/PR without required cases cannot pass. Validate actual customer artifact reachability before production use.

Native ACLs/selections gate history/output; leases reflect last verified access, not instantaneous mirroring. Historical packages retain original timestamps/versions; approvals/invoices require current evidence/policy. Explicit connector purge redacts dependent payloads and makes the affected project unavailable. Unrelated uploaded requirement originals and independent saved history remain stored. Purged-project recovery is not implemented.

Views are bounded to latest 100 contracts/milestones/history entries and 500 requirements/evidence; chat is narrower. Jobs use two workers, 5 active per owner, 150-second execution / 180-second claims, up to3 non-rate attempts and 1 hour age. Rate delays persist. No exactly-once provider guarantee is claimed.

PDFs use existing pypdf/core fonts; unsupported Unicode appears as visible escaped code points while JSON preserves exact Unicode. Fictional English PDFs were rendered/inspected, not every language/layout.

## Dependency audit remediation

The original delivery and completed code-check run 40173154053 failed dependency audits: 52 Python advisories across certifi 2026.7.22, filelock 4.0.4, Pillow 12.3.0 and pypdf 6.19.0, plus a critical Next 16.3.8 dependency. These were reproduced locally during the PR #1 follow-up, not suppressed.

The follow-up updates Next/eslint-config-next to 16.4.3, certifi to 2026.10.23, filelock to 4.2.4, Pillow to 13.0.0 and pypdf to 6.21.2, with secure minimums in the existing requirement inputs and regenerated hash locks. Vitest/coverage and affected development transitive packages receive compatible security updates too. Local locked Python, production npm and complete npm audits now report no known vulnerabilities. This is a timestamped audit result, not a permanent security guarantee. Full regression and actual CI/Sonar results are recorded separately in TEST_RESULTS.md and the PR; no audit ignore, force upgrade or skipped gate is used.

## Unverified external boundary

No production deployment/application database migration, live Clerk sign-in/lockout, provider consent/publication/quotas/events, real customer approval, paid xAI/Gemini request, Slack message, Drive write, invoice send or payment was performed. Local checks use disposable PostgreSQL and explicit provider/model/identity fixtures. Actual push/PR/CI status is recorded separately, never inferred from local success.
