"""Bounded GitHub observations; workflow success does not establish test execution."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, field_validator, model_validator

from backend.schemas.pipeline import Contract

GitHubStatus = Literal["queued", "requested", "waiting", "pending", "in_progress", "completed"]
GitHubConclusion = Literal[
    "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale", "startup_failure"
]
ReportStatus = Literal["not_requested", "unavailable", "parsed", "partial"]


class GitHubExpectedCase(Contract):
    suite: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=500)


class GitHubEvidenceRequest(Contract):
    repository_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    branch: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._/-]+$")
    sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    run_id: int = Field(gt=0)
    workflow_id: int = Field(gt=0)
    expected_suites: list[str] = Field(default_factory=list, max_length=30)
    expected_cases: list[GitHubExpectedCase] = Field(default_factory=list, max_length=200)
    artifact_names: list[str] = Field(default_factory=list, max_length=5)
    max_pages: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def bounded_names(self) -> "GitHubEvidenceRequest":
        if ".." in self.branch or self.branch.startswith("/") or self.branch.endswith("/"):
            raise ValueError("Choose a valid selected branch")
        for values in (self.expected_suites, self.artifact_names):
            if len(set(values)) != len(values) or any(
                not value.strip() or len(value) > 300 or any(ord(character) < 32 for character in value)
                for value in values
            ):
                raise ValueError("Evidence names must be unique bounded text")
        identities = [(case.suite, case.name) for case in self.expected_cases]
        if len(set(identities)) != len(identities):
            raise ValueError("Expected test cases must be unique")
        return self


class GitHubWorkflowRun(Contract):
    id: int = Field(gt=0)
    workflow_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    check_suite_id: int | None = Field(default=None, gt=0)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    head_branch: str = Field(min_length=1, max_length=255)
    status: GitHubStatus
    conclusion: GitHubConclusion | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    run_started_at: AwareDatetime | None = None
    html_url: str = Field(max_length=2048)


class GitHubJobStep(Contract):
    number: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1000)
    status: GitHubStatus
    conclusion: GitHubConclusion | None


class GitHubJob(Contract):
    id: int = Field(gt=0)
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    name: str = Field(min_length=1, max_length=1000)
    status: GitHubStatus
    conclusion: GitHubConclusion | None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    steps: list[GitHubJobStep] = Field(default_factory=list, max_length=200)


class GitHubCheckRun(Contract):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1000)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    status: GitHubStatus
    conclusion: GitHubConclusion | None
    check_suite_id: int = Field(gt=0)
    completed_at: AwareDatetime | None = None


class GitHubDeploymentStatus(Contract):
    id: int = Field(gt=0)
    state: Literal["error", "failure", "inactive", "in_progress", "queued", "pending", "success"]
    created_at: AwareDatetime
    environment_url: str | None = Field(default=None, max_length=2048)

    @field_validator("environment_url")
    @classmethod
    def safe_environment_url(cls, value: str | None) -> str | None:
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Deployment URL must be an ordinary HTTP(S) link")
        return value


class GitHubDeployment(Contract):
    id: int = Field(gt=0)
    sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    ref: str = Field(min_length=1, max_length=255)
    environment: str = Field(min_length=1, max_length=300)
    created_at: AwareDatetime
    statuses: list[GitHubDeploymentStatus] = Field(default_factory=list, max_length=100)
    statuses_complete: bool = False


class GitHubArtifact(Contract):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    size_in_bytes: int = Field(ge=0)
    expired: bool
    digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    workflow_run_id: int = Field(gt=0)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    report_status: ReportStatus = "not_requested"


class GitHubTestCase(Contract):
    suite: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=500)
    classname: str | None = Field(default=None, max_length=500)
    status: Literal["passed", "failed", "error", "skipped"]
    artifact_id: int = Field(gt=0)
    report_path: str = Field(min_length=1, max_length=500)
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GitHubEvidenceCoverage(Contract):
    jobs: bool = False
    checks: bool = False
    deployments: bool = False
    artifacts: bool = False


class GitHubEvidenceSnapshot(Contract):
    repository_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    repository_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str = Field(min_length=1, max_length=255)
    sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    observed_at: AwareDatetime
    selection_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    run: GitHubWorkflowRun
    jobs: list[GitHubJob] = Field(default_factory=list, max_length=500)
    checks: list[GitHubCheckRun] = Field(default_factory=list, max_length=500)
    deployments: list[GitHubDeployment] = Field(default_factory=list, max_length=100)
    artifacts: list[GitHubArtifact] = Field(default_factory=list, max_length=500)
    test_results: list[GitHubTestCase] = Field(default_factory=list, max_length=5000)
    test_report_status: ReportStatus = "not_requested"
    missing_suites: list[str] = Field(default_factory=list, max_length=30)
    missing_cases: list[GitHubExpectedCase] = Field(default_factory=list, max_length=200)
    coverage: GitHubEvidenceCoverage = Field(default_factory=GitHubEvidenceCoverage)
    limitations: list[str] = Field(default_factory=list, max_length=30)
