# Reproducible fictional demonstration

Run from the repository root with the existing Python environment:

```bash
myenev/bin/python -m scripts.proofos_demo
```

The command writes customer-safe JSON packages, searchable PDF packages, verification receipts and invoice-readiness receipts under `.local/proofos-demo/`. It reads `backend/tests/fixtures/proofos/website-contract.txt`, uses fixed timestamps and deterministic inputs, and makes no database, model, identity-provider, connector or external HTTP calls.

Every package visibly identifies the fictional simulation. Native observations and the separate customer decision are fixture inputs, not real GitHub execution or authenticated customer approval. A production workflow must obtain those authorities through the existing trusted gateway, owner-selected connectors, current canonical source checks and exact-package customer review.

- Scenario A: failed authentication tests, missing delivery guide and an internal discussion claim. Technical verification fails, delivery remains unverified and invoice eligibility is false.
- Scenario B: complete synthetic technical and delivery evidence passes; customer acceptance remains pending and invoice eligibility is false.
- Scenario C: the same verified prerequisites plus a distinct, exact-package, simulated authenticated customer decision make the simulated readiness receipt eligible. No invoice is issued or sent.

The PDF remains an immutable snapshot prepared while customer review was pending. Scenario C's later simulated decision is recorded separately in its readiness JSON; the package is not rewritten to invent an approval.

Verify the demonstration with:

```bash
myenev/bin/python -m pytest scripts/tests/test_proofos_demo.py --no-cov
```

No fixture is imported into application storage. Generated outputs remain ignored local artifacts.
