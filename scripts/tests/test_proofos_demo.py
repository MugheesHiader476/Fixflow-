import json
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from scripts.proofos_demo import generate, main, scenario


def test_synthetic_demo_distinguishes_failure_pending_acceptance_and_simulated_eligibility(tmp_path: Path) -> None:
    generate(tmp_path)
    first = json.loads((tmp_path / "scenario-a-verification.json").read_text())
    assert first["technical_status"] == "FAILED" and first["delivery_status"] == "UNVERIFIED"
    receipts = [json.loads((tmp_path / f"scenario-{name}-readiness.json").read_text()) for name in ("a", "b", "c")]
    assert [value["invoice_readiness"]["eligible"] for value in receipts] == [False, False, True]
    assert all("SYNTHETIC" in value["boundary"] for value in receipts)
    assert receipts[2]["simulated_customer_decision"]["simulation"] is True
    for name in ("a", "b", "c"):
        document = PdfReader(BytesIO((tmp_path / f"scenario-{name}-customer-package.pdf").read_bytes()))
        text = "\n".join(page.extract_text() for page in document.pages)
        assert "SYNTHETIC" in text and "ProofOS" in text
        assert "INTERNAL_FIXTURE_NOTE" not in text
    package = json.loads((tmp_path / "scenario-c-customer-package.json").read_text())
    assert "pending" in package["customer_acceptance"]["status"]


def test_synthetic_input_hashes_and_customer_packages_are_reproducible() -> None:
    first = scenario("B")
    second = scenario("B")
    assert first == second
    assert first is not second
    first["customer_hash"] = "modified fixture"
    assert second["customer_hash"] != first["customer_hash"]


def test_demo_cli_rejects_output_outside_ignored_private_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["proofos_demo", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
