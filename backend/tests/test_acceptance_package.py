import io
import json

import pdfplumber
import pytest
from pypdf import PdfReader

from backend.services.acceptance_package import customer_package, render_package


def package() -> dict[str, object]:
    return {
        "brand": "ProofOS",
        "project": {
            "name": "Customer Portal",
            "customer_name": "Example Customer",
            "private_budget": "INTERNAL_SECRET",
        },
        "milestone": {"code": "M1", "title": "Secure sign-in", "description": "Deliver authenticated access"},
        "contract": {"title": "Delivery agreement", "revision": "v1", "source_hash": "a" * 64, "source_version": 2},
        "verification": {
            "policy_version": "proofos-verification-v1",
            "technical_status": "PASSED",
            "delivery_status": "PASSED",
            "verified_at": "2026-10-25T12:00:00Z",
            "input_hash": "b" * 64,
            "checks": [
                {
                    "criterion_id": "auth",
                    "category": "technical",
                    "status": "PASSED",
                    "explanation": "INTERNAL_SECRET",
                    "evidence_ids": ["public", "private"],
                }
            ],
        },
        "requirements": [
            {
                "stable_id": "REQ-1",
                "original_text": "Users must authenticate.",
                "interpretation": "INTERNAL_SECRET",
                "acceptance_criteria": [
                    {
                        "id": "auth",
                        "description": "Authentication checks pass",
                        "category": "technical",
                        "kind": "ci",
                        "private_note": "INTERNAL_SECRET",
                    }
                ],
            }
        ],
        "evidence": [
            {
                "id": "public",
                "kind": "ci",
                "provider": "github",
                "status": "passed",
                "title": "Security suite",
                "customer_safe": True,
                "details": "INTERNAL_SECRET",
                "url": "https://github.com/example/repo/actions/runs/12",
            },
            {
                "id": "private",
                "kind": "discussion",
                "provider": "slack",
                "title": "INTERNAL_SECRET",
                "customer_safe": False,
            },
        ],
        "exceptions": ["INTERNAL_SECRET"],
        "internal_review": {"status": "approved", "reviewer": "INTERNAL_SECRET"},
        "customer_acceptance": {"status": "pending", "internal_token": "INTERNAL_SECRET"},
        "generated_at": "2026-10-25T12:00:00Z",
        "package_version": 1,
        "credentials": "INTERNAL_SECRET",
    }


def test_customer_allowlist_excludes_internal_content_and_private_evidence_references() -> None:
    payload = package()
    before = json.dumps(payload, sort_keys=True)
    safe = customer_package(payload)
    serialized = json.dumps(safe)
    assert "INTERNAL_SECRET" not in serialized
    assert '"private"' not in serialized
    assert "internal_review" not in safe
    assert "exceptions" not in safe
    assert "Users must authenticate." in serialized
    assert "Security suite" in serialized
    assert safe["customer_acceptance"] == {"status": "pending"}
    assert json.dumps(payload, sort_keys=True) == before


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/repo?token=PRIVATE_TOKEN",
        "https://github.com/repo#access_token=PRIVATE_TOKEN",
        "https://user:password@github.com/repo",
        "javascript:alert(1)",
        "https://[malformed",
    ],
)
def test_customer_evidence_never_exposes_credential_urls(url: str) -> None:
    payload = package()
    payload["evidence"] = [{"id": "safe", "customer_safe": True, "title": "Safe", "url": url}]
    assert "url" not in json.dumps(customer_package(payload))


def test_customer_flag_requires_an_actual_boolean() -> None:
    payload = package()
    payload["evidence"] = [{"id": "private", "customer_safe": "true", "title": "INTERNAL_SECRET"}]
    assert customer_package(payload)["evidence"] == []


def test_generated_pdf_is_searchable_and_contains_only_customer_projection() -> None:
    rendered = render_package(customer_package(package()))
    assert rendered.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(rendered))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "ProofOS" in text
    assert "Users must authenticate." in text
    assert "Security suite" in text
    assert "pending" in text
    assert "INTERNAL_SECRET" not in text
    assert "not an invoice or proof of payment" in " ".join(text.split())
    assert reader.metadata and reader.metadata.title == "ProofOS milestone acceptance package"


def test_pdf_wraps_long_content_over_pages_without_dropping_end_marker() -> None:
    payload = package()
    payload["requirements"] = [{"stable_id": "LONG", "original_text": "delivery evidence " * 2000 + "FINAL_MARKER"}]
    reader = PdfReader(io.BytesIO(render_package(payload)))
    assert len(reader.pages) > 2
    assert "FINAL_MARKER" in "\n".join(page.extract_text() for page in reader.pages)


def test_pdf_text_is_escaped_and_nonfinite_or_oversized_inputs_are_rejected() -> None:
    payload = package()
    payload["milestone"] = {"title": "Literal (parentheses) \\ slash <xml> " + chr(0x2264)}
    assert "Literal (parentheses)" in "\n".join(
        page.extract_text() for page in PdfReader(io.BytesIO(render_package(payload))).pages
    )
    with pytest.raises(ValueError):
        render_package({"invalid": float("nan")})
    with pytest.raises(ValueError, match="bounded rendering"):
        render_package({"oversized": "x" * 1_000_001})


def test_pdf_wide_glyphs_and_unbroken_identifiers_remain_inside_page_margins() -> None:
    payload = package()
    payload["requirements"] = [{"stable_id": "W" * 200, "original_text": "W" * 1000 + chr(0x2264) * 30}]
    rendered = render_package(payload)
    with pdfplumber.open(io.BytesIO(rendered)) as document:
        assert all(character["x1"] <= page.width - 47 for page in document.pages for character in page.chars)
        assert all(character["bottom"] < page.height - 30 for page in document.pages for character in page.chars)
