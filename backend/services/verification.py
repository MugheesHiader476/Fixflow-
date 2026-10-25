from datetime import datetime, timedelta

from backend.schemas.pipeline import digest
from backend.schemas.verification import (
    Category,
    CheckStatus,
    Criterion,
    CriterionResult,
    EvidenceFact,
    InvoiceReadiness,
    VerificationResult,
)

POLICY_VERSION = "proofos-verification-v1"
NATIVE_KINDS = frozenset({"ci", "deployment", "review"})


def _related(criterion: Criterion, fact: EvidenceFact) -> bool:
    if criterion.kind != fact.kind:
        return False
    if fact.requirement_ids:
        return criterion.id in fact.requirement_ids
    return criterion.kind in NATIVE_KINDS


def _identity_matches(criterion: Criterion, fact: EvidenceFact) -> bool:
    return all(
        expected is None or expected == getattr(fact, field)
        for field in ("repository_id", "branch", "sha", "workflow_id", "run_id", "suite", "environment")
        if (expected := getattr(criterion, field)) is not None
    )


def _trusted(criterion: Criterion, fact: EvidenceFact, now: datetime) -> bool:
    if fact.observed_at > now or (fact.event_at is not None and fact.event_at > now):
        return False
    oldest = now - timedelta(hours=criterion.max_age_hours)
    if fact.observed_at < oldest or (fact.event_at or fact.observed_at) < oldest:
        return False
    if criterion.kind in NATIVE_KINDS:
        return fact.provider == "github" and fact.verified_native and fact.event_at is not None
    if criterion.kind == "document":
        return bool(fact.source_id and fact.source_hash)
    return fact.verified_native


def _check(criterion: Criterion, evidence: list[EvidenceFact], approved: bool, now: datetime) -> CriterionResult:
    def result(status: CheckStatus, explanation: str, facts: list[EvidenceFact] | None = None) -> CriterionResult:
        return CriterionResult(
            criterion_id=criterion.id,
            category=criterion.category,
            status=status,
            explanation=explanation,
            evidence_ids=sorted(fact.id for fact in (facts or [])),
        )

    if not approved:
        return result("BLOCKED", "The contractual scope requires approval before verification.")
    if criterion.category == "customer":
        return result("BLOCKED", "Customer acceptance requires a separate authenticated customer decision.")
    if criterion.category == "technical" and criterion.mandatory and criterion.kind in {"manual", "discussion"}:
        return result("UNVERIFIED", "Discussion or manual claims cannot verify a mandatory technical criterion.")
    if criterion.kind in NATIVE_KINDS and not (criterion.repository_id and criterion.sha):
        return result("UNVERIFIED", "An exact repository and commit identity must be approved for native verification.")
    if criterion.kind == "ci" and not (
        criterion.workflow_id and criterion.branch and criterion.suite and criterion.cases
    ):
        return result("UNVERIFIED", "The required branch, workflow, test suite and individual cases must be specified.")
    if criterion.kind == "deployment" and not criterion.environment:
        return result("UNVERIFIED", "The required deployment environment must be specified.")
    related = [fact for fact in evidence if _related(criterion, fact)]
    matched = [fact for fact in related if _identity_matches(criterion, fact) and _trusted(criterion, fact, now)]
    failed = [fact for fact in matched if fact.status == "failed"]
    if failed:
        return result("FAILED", "Authoritative evidence reports a failure for the required scope.", failed)
    if any(fact.status in {"pending", "unknown"} for fact in matched):
        return result("UNVERIFIED", "Evidence for the required scope is pending or has an unknown outcome.", matched)
    if any(fact.status == "skipped" for fact in matched) and not criterion.allow_skipped:
        return result(
            "UNVERIFIED", "Required checks include skipped results that this criterion does not allow.", matched
        )
    complete = [fact for fact in matched if fact.complete]
    passing = [
        fact for fact in complete if fact.status == "passed" or (fact.status == "skipped" and criterion.allow_skipped)
    ]
    if criterion.cases:
        passing = [fact for fact in passing if set(criterion.cases).issubset(fact.cases)]
    if passing:
        return result("PASSED", "Complete, current evidence satisfies the required identity and scope.", passing)
    return result(
        "UNVERIFIED",
        "Complete, current evidence matching the required identity, provenance and scope is unavailable.",
        matched,
    )


def _aggregate(category: Category, criteria: list[Criterion], checks: list[CriterionResult]) -> CheckStatus:
    mandatory_ids = {criterion.id for criterion in criteria if criterion.category == category and criterion.mandatory}
    statuses = {check.status for check in checks if check.criterion_id in mandatory_ids}
    if not statuses:
        return "NOT_APPLICABLE"
    for status in ("FAILED", "BLOCKED", "UNVERIFIED"):
        if status in statuses:
            return status
    return "PASSED"


def verify(
    criteria: list[Criterion], evidence: list[EvidenceFact], *, approved: bool, now: datetime
) -> VerificationResult:
    if now.utcoffset() is None:
        raise ValueError("Verification time requires an explicit UTC offset")
    if len(criteria) > 500 or len(evidence) > 2000:
        raise ValueError("Verification input exceeds the bounded evaluation size")
    if len({criterion.id for criterion in criteria}) != len(criteria):
        raise ValueError("Criterion identities must be unique")
    if len({fact.id for fact in evidence}) != len(evidence):
        raise ValueError("Evidence identities must be unique")
    ordered_criteria = sorted(criteria, key=lambda criterion: criterion.id)
    ordered_evidence = sorted(evidence, key=lambda fact: fact.id)
    checks = [_check(criterion, ordered_evidence, approved, now) for criterion in ordered_criteria]
    has_mandatory_scope = any(
        criterion.mandatory and criterion.category in {"technical", "delivery"} for criterion in ordered_criteria
    )
    return VerificationResult(
        policy_version=POLICY_VERSION,
        technical_status=_aggregate("technical", ordered_criteria, checks) if has_mandatory_scope else "UNVERIFIED",
        delivery_status=_aggregate("delivery", ordered_criteria, checks) if has_mandatory_scope else "UNVERIFIED",
        checks=checks,
        verified_at=now,
        input_hash=digest(
            {
                "policy_version": POLICY_VERSION,
                "approved": approved,
                "verified_at": now.isoformat(),
                "criteria": [criterion.model_dump(mode="json") for criterion in ordered_criteria],
                "evidence": [fact.model_dump(mode="json") for fact in ordered_evidence],
            }
        ),
    )


def invoice_readiness(
    verification: VerificationResult,
    *,
    internal_approved: bool,
    customer_approved: bool,
    contractual_terms_satisfied: bool,
) -> InvoiceReadiness:
    reasons = []
    if verification.policy_version != POLICY_VERSION:
        reasons.append("Verification must use the current deterministic policy.")
    if verification.technical_status not in {"PASSED", "NOT_APPLICABLE"}:
        reasons.append("Mandatory technical verification has not passed.")
    if verification.delivery_status not in {"PASSED", "NOT_APPLICABLE"}:
        reasons.append("Mandatory delivery verification has not passed.")
    if not internal_approved:
        reasons.append("Internal approval is required.")
    if not customer_approved:
        reasons.append("An authenticated customer approval is required.")
    if not contractual_terms_satisfied:
        reasons.append("Applicable contractual payment conditions must be satisfied.")
    return InvoiceReadiness(eligible=not reasons, reasons=reasons)
