"""Generate reproducible fictional A/B/C verification packages without databases or external calls."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.schemas.pipeline import digest
from backend.schemas.verification import Criterion, EvidenceFact
from backend.services.acceptance_package import customer_package, render_package
from backend.services.verification import invoice_readiness, verify

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "backend/tests/fixtures/proofos/website-contract.txt"
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
SHA = "a" * 40
BOUNDARY = (
    "SYNTHETIC FIXTURE ONLY: no live provider calls, real authentication, customer acceptance, invoice or payment"
)


def inputs() -> tuple[list[Criterion], list[EvidenceFact], list[dict[str, object]], str]:
    text = FIXTURE.read_text()
    wording = {
        line.partition(": ")[0]: line.partition(": ")[2] for line in text.splitlines() if line.startswith("REQ-")
    }
    criteria = [
        Criterion(
            id="REQ-AUTH",
            description=wording["REQ-AUTH"],
            category="technical",
            kind="ci",
            repository_id="42",
            branch="main",
            sha=SHA,
            workflow_id=7,
            suite="authentication",
            cases=["login", "tenant_isolation"],
        ),
        Criterion(
            id="REQ-DEPLOY",
            description=wording["REQ-DEPLOY"],
            category="technical",
            kind="deployment",
            repository_id="42",
            branch="main",
            sha=SHA,
            environment="production",
        ),
        Criterion(id="REQ-GUIDE", description=wording["REQ-GUIDE"], category="delivery", kind="document"),
        Criterion(id="REQ-ACCEPT", description=wording["REQ-ACCEPT"], category="customer", kind="manual"),
    ]
    common: dict[str, object] = {
        "provider": "github",
        "repository_id": "42",
        "branch": "main",
        "sha": SHA,
        "status": "passed",
        "observed_at": NOW,
        "event_at": NOW - timedelta(hours=1),
        "complete": True,
        "verified_native": True,
        "customer_safe": True,
        "details": BOUNDARY,
    }
    facts = [
        EvidenceFact.model_validate(
            {
                **common,
                "id": "synthetic-ci",
                "kind": "ci",
                "workflow_id": 7,
                "run_id": 11,
                "suite": "authentication",
                "cases": ["login", "tenant_isolation"],
                "title": "SYNTHETIC: authentication test report",
            }
        ),
        EvidenceFact.model_validate(
            {
                **common,
                "id": "synthetic-deployment",
                "kind": "deployment",
                "environment": "production",
                "title": "SYNTHETIC: production deployment observation",
            }
        ),
        EvidenceFact(
            id="synthetic-guide",
            kind="document",
            provider="synthetic-canonical-fixture",
            status="passed",
            observed_at=NOW,
            source_id=str(uuid5(NAMESPACE_URL, "proofos:synthetic:delivery-guide")),
            source_hash=hashlib.sha256(b"Synthetic delivery guide fixture, revision 1").hexdigest(),
            title="SYNTHETIC: versioned delivery guide",
            complete=True,
            customer_safe=True,
            requirement_ids=["REQ-GUIDE"],
            details=BOUNDARY,
        ),
    ]
    requirements: list[dict[str, object]] = [
        {
            "stable_id": criterion.id,
            "original_text": criterion.description,
            "acceptance_criteria": [criterion.model_dump(mode="json")],
        }
        for criterion in criteria
    ]
    return criteria, facts, requirements, hashlib.sha256(text.encode()).hexdigest()


def scenario(name: str) -> dict[str, object]:
    criteria, evidence, requirements, source_hash = inputs()
    if name == "A":
        evidence = [
            evidence[0].model_copy(update={"status": "failed"}),
            evidence[1],
            EvidenceFact(
                id="synthetic-slack-claim",
                kind="discussion",
                provider="slack",
                status="passed",
                observed_at=NOW,
                title="SYNTHETIC INTERNAL: participant claims the work is complete",
                details="INTERNAL_FIXTURE_NOTE",
                complete=True,
                verified_native=True,
                requirement_ids=["REQ-AUTH", "REQ-GUIDE"],
                customer_safe=False,
            ),
        ]
    verification = verify(criteria, evidence, approved=True, now=NOW)
    payload: dict[str, object] = {
        "brand": "ProofOS",
        "project": {"name": "SYNTHETIC Example Website", "customer_name": "Fictional Example Customer"},
        "milestone": {"code": "DEMO-" + name, "title": "SYNTHETIC scenario " + name, "description": BOUNDARY},
        "contract": {
            "title": "Fictional website delivery fixture",
            "revision": 1,
            "source_hash": source_hash,
            "source_version": 1,
        },
        "verification": verification.model_dump(mode="json"),
        "requirements": requirements,
        "evidence": [fact.model_dump(mode="json") for fact in evidence],
        "exceptions": ["INTERNAL_FIXTURE_NOTE"],
        "internal_review": {"status": "simulated-approved"},
        "customer_acceptance": {"status": "pending in immutable package snapshot"},
        "generated_at": NOW.isoformat(),
        "package_version": 1,
    }
    customer = customer_package(payload)
    customer_hash = digest(customer)
    decision: dict[str, object] = {
        "simulation": True,
        "authentication": "fixture simulation of a separate authenticated customer; no real identity verified",
        "actor": "user_synthetic_customer",
        "owner": "user_synthetic_supplier",
        "decision": "accepted" if name == "C" else "pending",
        "package_hash": customer_hash,
        "decided_at": NOW.isoformat() if name == "C" else None,
    }
    simulated_customer_approved = bool(
        decision["decision"] == "accepted"
        and decision["actor"] != decision["owner"]
        and decision["package_hash"] == customer_hash
        and decision["decided_at"]
    )
    readiness = invoice_readiness(
        verification,
        internal_approved=True,
        customer_approved=simulated_customer_approved,
        contractual_terms_satisfied=True,
    )
    return {
        "scenario": name,
        "boundary": BOUNDARY,
        "verification": verification.model_dump(mode="json"),
        "customer_package": customer,
        "customer_hash": customer_hash,
        "simulated_customer_decision": decision,
        "invoice_readiness": readiness.model_dump(mode="json"),
    }


def _write(path: Path, content: bytes) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(content)


def generate(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    summaries = []
    for name in ("A", "B", "C"):
        value = scenario(name)
        package = value["customer_package"]
        if not isinstance(package, dict):
            raise ValueError("Invalid synthetic package")
        prefix = "scenario-" + name.lower()
        for suffix, content in (
            ("verification", value["verification"]),
            ("customer-package", package),
            (
                "readiness",
                {
                    "boundary": BOUNDARY,
                    "simulated_customer_decision": value["simulated_customer_decision"],
                    "invoice_readiness": value["invoice_readiness"],
                },
            ),
        ):
            _write(output / f"{prefix}-{suffix}.json", (json.dumps(content, sort_keys=True, indent=2) + "\n").encode())
        _write(output / f"{prefix}-customer-package.pdf", render_package(package))
        summaries.append(
            {key: value[key] for key in ("scenario", "customer_hash", "verification", "invoice_readiness")}
        )
    summary: dict[str, object] = {"boundary": BOUNDARY, "generated_at": NOW.isoformat(), "scenarios": summaries}
    _write(output / "summary.json", (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/proofos-demo")
    options = parser.parse_args()
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / ".local") or output == ROOT / ".local":
        parser.error("Demo output must be a dedicated directory inside the ignored .local directory")
    summary = generate(output)
    print(json.dumps({"boundary": summary["boundary"], "output": str(output), "scenarios": 3}, sort_keys=True))


if __name__ == "__main__":
    main()
