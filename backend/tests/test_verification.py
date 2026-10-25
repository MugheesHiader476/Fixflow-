from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.schemas.verification import Criterion, EvidenceFact
from backend.services.verification import invoice_readiness, verify

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
SHA = "a" * 40


def criteria() -> list[Criterion]:
    return [
        Criterion(
            id="auth",
            description="Authentication tests pass",
            category="technical",
            kind="ci",
            repository_id="42",
            branch="main",
            sha=SHA,
            suite="security",
            workflow_id=7,
            cases=["login", "owner_isolation"],
        ),
        Criterion(id="guide", description="Delivery guide exists", category="delivery", kind="document"),
        Criterion(id="accept", description="Customer accepts delivery", category="customer", kind="manual"),
    ]


def facts() -> list[EvidenceFact]:
    return [
        EvidenceFact(
            id="run",
            kind="ci",
            provider="github",
            repository_id="42",
            branch="main",
            sha=SHA,
            suite="security",
            workflow_id=7,
            cases=["login", "owner_isolation"],
            status="passed",
            observed_at=NOW,
            event_at=NOW - timedelta(hours=1),
            title="Security suite",
            complete=True,
            verified_native=True,
        ),
        EvidenceFact(
            id="document",
            kind="document",
            provider="upload",
            status="passed",
            observed_at=NOW,
            source_id="source-1",
            source_hash="b" * 64,
            title="Delivery guide",
            complete=True,
            requirement_ids=["guide"],
        ),
    ]


def test_failing_auth_missing_document_and_slack_claim_never_create_readiness() -> None:
    evidence = [
        facts()[0].model_copy(update={"status": "failed"}),
        EvidenceFact(
            id="slack",
            kind="discussion",
            provider="slack",
            status="passed",
            observed_at=NOW,
            title="Everything is done",
            complete=True,
            verified_native=True,
            requirement_ids=["auth", "guide"],
        ),
    ]
    result = verify(criteria(), evidence, approved=True, now=NOW)
    assert result.technical_status == "FAILED"
    assert result.delivery_status == "UNVERIFIED"
    assert not invoice_readiness(
        result, internal_approved=True, customer_approved=True, contractual_terms_satisfied=True
    ).eligible


def test_all_technical_and_delivery_evidence_pass_with_customer_acceptance_pending() -> None:
    result = verify(criteria(), facts(), approved=True, now=NOW)
    assert result.technical_status == result.delivery_status == "PASSED"
    assert next(check for check in result.checks if check.criterion_id == "accept").status == "BLOCKED"
    readiness = invoice_readiness(
        result, internal_approved=True, customer_approved=False, contractual_terms_satisfied=True
    )
    assert not readiness.eligible
    assert readiness.reasons == ["An authenticated customer approval is required."]


def test_invoice_readiness_requires_each_independent_approval_and_contract_condition() -> None:
    result = verify(criteria(), facts(), approved=True, now=NOW)
    assert invoice_readiness(
        result, internal_approved=True, customer_approved=True, contractual_terms_satisfied=True
    ).eligible
    for missing in ("internal_approved", "customer_approved", "contractual_terms_satisfied"):
        inputs = {
            "internal_approved": True,
            "customer_approved": True,
            "contractual_terms_satisfied": True,
            missing: False,
        }
        assert not invoice_readiness(result, **inputs).eligible


@pytest.mark.parametrize(
    "update",
    [
        {"repository_id": "other"},
        {"branch": "feature"},
        {"sha": "c" * 40},
        {"suite": None},
        {"workflow_id": None},
        {"workflow_id": 8},
        {"cases": ["login"]},
        {"verified_native": False},
        {"provider": "slack"},
        {"complete": False},
        {"event_at": None},
        {"status": "pending"},
        {"status": "unknown"},
        {"status": "skipped"},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"event_at": NOW + timedelta(seconds=1)},
        {"event_at": NOW - timedelta(hours=169)},
        {"observed_at": NOW - timedelta(hours=169)},
    ],
)
def test_missing_incomplete_stale_future_or_wrong_scope_never_passes(update: dict[str, object]) -> None:
    evidence = [facts()[0].model_copy(update=update), facts()[1]]
    assert verify(criteria(), evidence, approved=True, now=NOW).technical_status == "UNVERIFIED"


def test_conflicting_native_outcomes_fail_closed() -> None:
    evidence = [*facts(), facts()[0].model_copy(update={"id": "conflict", "status": "failed"})]
    assert verify(criteria(), evidence, approved=True, now=NOW).technical_status == "FAILED"


def test_optional_success_cannot_override_a_mandatory_failure() -> None:
    required = [
        *criteria(),
        Criterion(id="optional", description="Optional note", category="technical", kind="manual", mandatory=False),
    ]
    evidence = [
        facts()[0].model_copy(update={"status": "failed"}),
        facts()[1],
        EvidenceFact(
            id="optional",
            kind="manual",
            provider="review",
            title="Optional",
            status="passed",
            observed_at=NOW,
            complete=True,
            verified_native=True,
            requirement_ids=["optional"],
        ),
    ]
    assert verify(required, evidence, approved=True, now=NOW).technical_status == "FAILED"


@pytest.mark.parametrize("kind", ["manual", "discussion"])
def test_manual_or_discussion_claim_cannot_verify_required_technical_work(kind: str) -> None:
    criterion = Criterion.model_validate(
        {"id": "claim", "description": "Claims", "category": "technical", "kind": kind}
    )
    fact = EvidenceFact.model_validate(
        {
            "id": "claim",
            "kind": kind,
            "provider": "slack",
            "title": "All done",
            "status": "passed",
            "observed_at": NOW,
            "complete": True,
            "verified_native": True,
            "requirement_ids": ["claim"],
        }
    )
    assert verify([criterion], [fact], approved=True, now=NOW).technical_status == "UNVERIFIED"


@pytest.mark.parametrize(
    "update", [{"source_id": None}, {"source_hash": None}, {"complete": False}, {"requirement_ids": []}]
)
def test_delivery_document_requires_canonical_identity_and_explicit_association(update: dict[str, object]) -> None:
    assert (
        verify(criteria(), [facts()[0], facts()[1].model_copy(update=update)], approved=True, now=NOW).delivery_status
        == "UNVERIFIED"
    )


def test_scope_approval_duplicate_identities_and_timezone_are_enforced() -> None:
    blocked = verify(criteria(), facts(), approved=False, now=NOW)
    assert blocked.technical_status == blocked.delivery_status == "BLOCKED"
    with pytest.raises(ValueError, match="Criterion identities"):
        verify([*criteria(), criteria()[0]], facts(), approved=True, now=NOW)
    with pytest.raises(ValueError, match="Evidence identities"):
        verify(criteria(), [*facts(), facts()[0]], approved=True, now=NOW)
    with pytest.raises(ValueError, match="UTC offset"):
        verify(criteria(), facts(), approved=True, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="UTC offset"):
        EvidenceFact.model_validate({**facts()[0].model_dump(), "observed_at": NOW.replace(tzinfo=None)})


def test_hash_is_stable_for_input_order_sensitive_to_changes_and_does_not_mutate_inputs() -> None:
    required, evidence = criteria(), facts()
    original = [fact.model_dump(mode="json") for fact in evidence]
    first = verify(required, evidence, approved=True, now=NOW)
    reordered = verify(list(reversed(required)), list(reversed(evidence)), approved=True, now=NOW)
    changed = verify(
        required, [evidence[0].model_copy(update={"status": "failed"}), evidence[1]], approved=True, now=NOW
    )
    assert first == reordered
    assert first.input_hash != changed.input_hash
    assert [fact.model_dump(mode="json") for fact in evidence] == original


def test_empty_requirements_and_old_policy_cannot_create_invoice_eligibility() -> None:
    result = verify([], [], approved=True, now=NOW)
    assert not invoice_readiness(
        result, internal_approved=True, customer_approved=True, contractual_terms_satisfied=True
    ).eligible
    valid = verify(criteria(), facts(), approved=True, now=NOW).model_copy(update={"policy_version": "retired"})
    assert not invoice_readiness(
        valid, internal_approved=True, customer_approved=True, contractual_terms_satisfied=True
    ).eligible


@pytest.mark.parametrize("update", [{"suite": None}, {"cases": []}, {"workflow_id": None}, {"branch": None}])
def test_ci_requires_explicit_approved_workflow_branch_suite_and_cases(update: dict[str, object]) -> None:
    required = criteria()
    required[0] = required[0].model_copy(update=update)
    assert verify(required, facts(), approved=True, now=NOW).technical_status == "UNVERIFIED"


@pytest.mark.parametrize(
    "environment,status,expected",
    [
        ("production", "passed", "PASSED"),
        ("staging", "passed", "UNVERIFIED"),
        ("production", "pending", "UNVERIFIED"),
        ("production", "failed", "FAILED"),
    ],
)
def test_deployment_verification_binds_exact_environment(environment: str, status: str, expected: str) -> None:
    criterion = Criterion(
        id="deploy",
        description="Production deployment",
        category="technical",
        kind="deployment",
        repository_id="42",
        sha=SHA,
        environment="production",
    )
    fact = EvidenceFact.model_validate(
        {
            "id": "deploy",
            "kind": "deployment",
            "provider": "github",
            "repository_id": "42",
            "sha": SHA,
            "environment": environment,
            "status": status,
            "observed_at": NOW,
            "event_at": NOW,
            "title": "Deployment status",
            "complete": True,
            "verified_native": True,
        }
    )
    assert verify([criterion], [fact], approved=True, now=NOW).technical_status == expected
    missing = criterion.model_copy(update={"environment": None})
    assert verify([missing], [fact], approved=True, now=NOW).technical_status == "UNVERIFIED"


def test_absent_category_is_not_applicable_but_empty_or_customer_only_scope_cannot_invoice() -> None:
    result = verify([criteria()[1]], [facts()[1]], approved=True, now=NOW)
    assert result.technical_status == "NOT_APPLICABLE" and result.delivery_status == "PASSED"
    assert invoice_readiness(
        result, internal_approved=True, customer_approved=True, contractual_terms_satisfied=True
    ).eligible
    customer_only = verify([criteria()[2]], [], approved=True, now=NOW)
    assert not invoice_readiness(
        customer_only, internal_approved=True, customer_approved=True, contractual_terms_satisfied=True
    ).eligible
