"""Owner-scoped contractual delivery contracts; client statements never establish verification."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.schemas.pipeline import Contract
from backend.schemas.verification import Criterion


class ProjectCreate(Contract):
    name: str = Field(min_length=1, max_length=200)
    customer_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    customer_approver_id: str | None = Field(default=None, pattern=r"^user_[A-Za-z0-9_-]{1,190}$")
    github_account_id: UUID | None = None
    repository_id: str | None = Field(default=None, pattern=r"^[0-9]{1,30}$")
    repository_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str = Field(default="main", min_length=1, max_length=200)

    @model_validator(mode="after")
    def repository_identity(self) -> "ProjectCreate":
        if any([self.github_account_id, self.repository_id, self.repository_name]) and not all(
            [self.github_account_id, self.repository_id, self.repository_name]
        ):
            raise ValueError("Choose the account and exact repository identity together")
        return self


class ContractCreate(Contract):
    source_id: UUID
    title: str = Field(min_length=1, max_length=200)


class EngineeringLink(Contract):
    kind: Literal["issue", "pull_request", "commit", "release", "task"]
    external_id: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https://[^\s]+$")
    title: str = Field(min_length=1, max_length=300)


class RequirementReview(Contract):
    interpretation: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[Criterion] = Field(min_length=1, max_length=30)
    ambiguities: list[str] = Field(default_factory=list, max_length=30)
    engineering_links: list[EngineeringLink] = Field(default_factory=list, max_length=50)
    approve: bool = False

    @model_validator(mode="after")
    def distinct_criteria(self) -> "RequirementReview":
        identifiers = [criterion.id for criterion in self.acceptance_criteria]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Acceptance criterion IDs must be unique")
        if self.approve and self.ambiguities:
            raise ValueError("Resolve ambiguities before approval")
        return self


class RequirementAssign(Contract):
    milestone_id: UUID


class MilestoneCreate(Contract):
    contract_id: UUID
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    expected_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    amount_minor: int | None = Field(default=None, ge=1, le=1_000_000_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    payment_terms: str | None = Field(default=None, min_length=1, max_length=2000)


class CollectEvidence(Contract):
    run_id: int = Field(gt=0)
    workflow_id: int = Field(gt=0)
    artifact_names: list[str] = Field(default_factory=list, max_length=5)


class SourceEvidence(Contract):
    source_id: UUID
    requirement_ids: list[UUID] = Field(min_length=1, max_length=100)
    kind: Literal["document", "discussion"]
    customer_safe: bool = False


class PackageReview(Contract):
    decision: Literal["approved", "rejected"]
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalCreate(Contract):
    expires_in_hours: int = Field(default=72, ge=1, le=168)


class ApprovalDecision(Contract):
    decision: Literal["accepted", "rejected", "rework_requested"]
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


class DomainIdentifier(Contract):
    id: UUID


class EmptyArgs(Contract):
    pass


class ExtractionArgs(Contract):
    project_id: UUID
    source_id: UUID
    title: str = Field(min_length=1, max_length=200)


class ProofJobReceipt(Contract):
    id: UUID
    project_id: UUID
    kind: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    error: str | None
    result: dict[str, object]
    created_at: datetime
