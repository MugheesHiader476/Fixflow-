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

## Baseline audit failure

Before and after changes, the locked dependency audit reports52 vulnerabilities across certifi 2026.7.22, filelock 4.0.4, Pillow 12.3.0 and pypdf 6.19.0. No dependency upgrade or disabled gate conceals this. Remediation/regression testing blocks production release. Detailed current advisories are in ignored `.local/proofos-final-pip-audit.json`.

The production npm audit also fails for the inherited Next 16.3.8 dependency: one critical package, three advisory entries. This branch changes package branding only, not dependency versions. A separately reviewed Next security update and full regression run are required before production deployment. Details: ignored `.local/proofos-final-npm-audit.json`.

## Unverified external boundary

No production deployment/application database migration, live Clerk sign-in/lockout, provider consent/publication/quotas/events, real customer approval, paid xAI/Gemini request, Slack message, Drive write, invoice send or payment was performed. Local checks use disposable PostgreSQL and explicit provider/model/identity fixtures. Actual push/PR/CI status is recorded separately, never inferred from local success.
