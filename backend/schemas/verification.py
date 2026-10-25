from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from backend.schemas.pipeline import Contract

EvidenceKind = Literal["ci", "deployment", "document", "review", "discussion", "manual"]
Category = Literal["technical", "delivery", "customer"]
CheckStatus = Literal["PASSED", "FAILED", "UNVERIFIED", "BLOCKED", "NOT_APPLICABLE"]


class VerificationContract(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Criterion(VerificationContract):
    id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    category: Category
    kind: EvidenceKind
    mandatory: bool = Field(default=True, strict=True)
    repository_id: str | None = Field(default=None, min_length=1, max_length=300)
    branch: str | None = Field(default=None, min_length=1, max_length=300)
    sha: str | None = Field(default=None, min_length=1, max_length=100)
    workflow_id: int | None = Field(default=None, ge=1)
    run_id: int | None = Field(default=None, ge=1)
    suite: str | None = Field(default=None, min_length=1, max_length=300)
    environment: str | None = Field(default=None, min_length=1, max_length=300)
    cases: list[str] = Field(default_factory=list, max_length=1000)
    max_age_hours: int = Field(default=168, ge=1, le=8760)
    allow_skipped: bool = Field(default=False, strict=True)

    @field_validator("id", "description", "repository_id", "branch", "sha", "suite", "environment")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Verification text must not be blank")
        return value

    @field_validator("cases")
    @classmethod
    def valid_cases(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in value) or len(set(value)) != len(value):
            raise ValueError("Test cases must be unique nonblank names")
        return value


class EvidenceFact(VerificationContract):
    id: str = Field(min_length=1, max_length=200)
    kind: EvidenceKind
    provider: str = Field(min_length=1, max_length=100)
    repository_id: str | None = Field(default=None, min_length=1, max_length=300)
    branch: str | None = Field(default=None, min_length=1, max_length=300)
    sha: str | None = Field(default=None, min_length=1, max_length=100)
    workflow_id: int | None = Field(default=None, ge=1)
    run_id: int | None = Field(default=None, ge=1)
    suite: str | None = Field(default=None, min_length=1, max_length=300)
    environment: str | None = Field(default=None, min_length=1, max_length=300)
    cases: list[str] = Field(default_factory=list, max_length=1000)
    status: Literal["passed", "failed", "pending", "unknown", "skipped"]
    observed_at: datetime
    event_at: datetime | None = None
    source_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    external_id: str | None = Field(default=None, min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    title: str = Field(min_length=1, max_length=500)
    details: str = Field(default="", max_length=20000)
    complete: bool = Field(default=False, strict=True)
    verified_native: bool = Field(default=False, strict=True)
    customer_safe: bool = Field(default=False, strict=True)
    requirement_ids: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("observed_at", "event_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Evidence timestamps require an explicit UTC offset")
        return value

    @field_validator("cases", "requirement_ids")
    @classmethod
    def nonblank_names(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in value) or len(set(value)) != len(value):
            raise ValueError("Evidence names must be unique and nonblank")
        return value


class CriterionResult(VerificationContract):
    criterion_id: str
    category: Category
    status: CheckStatus
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class VerificationResult(VerificationContract):
    policy_version: str
    technical_status: CheckStatus
    delivery_status: CheckStatus
    checks: list[CriterionResult]
    verified_at: datetime
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class InvoiceReadiness(VerificationContract):
    eligible: bool
    reasons: list[str]
