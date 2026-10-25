"""Exact GitHub identity binding and safe test evidence without live credentials."""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from backend.connectors.base import OAuthConnector
from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError
from backend.connectors.github import GitHubConnector
from backend.connectors.github_evidence import junit_archive, junit_report
from backend.connectors.http import ProviderHttp
from backend.connectors.workspace import NativeWorkspace
from backend.db.models import ConnectorAccount
from backend.schemas.connectors import Selection
from backend.schemas.github_evidence import GitHubEvidenceRequest, GitHubExpectedCase

pytestmark = pytest.mark.anyio
SHA = "a" * 40
STAMP = "2026-10-25T00:00:00Z"


def archive_bytes(report: bytes, path: str = "reports/junit.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(path, report)
    return buffer.getvalue()


@dataclass
class EvidenceWorld:
    run: dict[str, object] = field(default_factory=lambda: {
        "id": 10, "workflow_id": 20, "run_attempt": 1, "head_sha": SHA, "head_branch": "main",
        "status": "completed", "conclusion": "success", "created_at": STAMP, "updated_at": STAMP,
        "run_started_at": STAMP, "repository": {"id": 1}, "head_repository": {"id": 1},
    })
    job: dict[str, object] = field(default_factory=lambda: {
        "id": 30, "run_id": 10, "head_sha": SHA, "name": "authentication", "status": "completed",
        "conclusion": "success", "started_at": STAMP, "completed_at": STAMP,
        "steps": [{"number": 1, "name": "Run mandatory tests", "status": "completed", "conclusion": "success"}],
    })
    check: dict[str, object] = field(default_factory=lambda: {
        "id": 40, "name": "authentication", "head_sha": SHA, "status": "completed", "conclusion": "success",
        "check_suite": {"id": 50}, "completed_at": STAMP,
    })
    artifact: dict[str, object] = field(default_factory=lambda: {
        "id": 60, "name": "test-results", "size_in_bytes": 100, "expired": False,
        "workflow_run": {"id": 10, "head_sha": SHA},
    })
    report: bytes = b'<testsuite name="authentication"><testcase name="sign in"/></testsuite>'
    redirect: bool = False
    change_run: bool = False
    change_head: bool = False
    head: str = SHA
    truncated_jobs: bool = False
    run_reads: int = 0
    branch_reads: int = 0
    requests: list[httpx.Request] = field(default_factory=list)

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        repo = {"id": 1, "full_name": "owner/repo", "default_branch": "main", "private": True}
        if path == "/repositories/1":
            return httpx.Response(200, json=repo)
        if path.endswith("/installation"):
            return httpx.Response(200, json={"id": 9, "app_id": 7})
        if path == "/app/installations/9/access_tokens":
            body = json.loads(request.content)
            assert body["repository_ids"] == [1]
            assert all(value == "read" for value in body["permissions"].values())
            return httpx.Response(201, json={"token": "installation-test-token"})
        if path == "/repos/owner/repo/branches/main":
            self.branch_reads += 1
            head = "b" * 40 if self.change_head and self.branch_reads > 1 else self.head
            return httpx.Response(200, json={"name": "main", "commit": {"sha": head}})
        if path == "/repos/owner/repo/actions/runs/10":
            self.run_reads += 1
            run = {**self.run, "run_attempt": 2} if self.change_run and self.run_reads > 1 else self.run
            return httpx.Response(200, json=run)
        if path.startswith("/repos/owner/repo/actions/runs/10/attempts/"):
            return httpx.Response(200, json={"total_count": 101 if self.truncated_jobs else 1, "jobs": [self.job]})
        if path == f"/repos/owner/repo/commits/{SHA}/check-runs":
            return httpx.Response(200, json={"total_count": 1, "check_runs": [self.check]})
        if path == "/repos/owner/repo/deployments":
            return httpx.Response(200, json=[{
                "id": 70, "sha": SHA, "ref": "main", "environment": "staging", "created_at": STAMP,
            }])
        if path == "/repos/owner/repo/deployments/70/statuses":
            return httpx.Response(200, json=[{
                "id": 80, "state": "success", "created_at": STAMP, "environment_url": "https://staging.example.test",
            }])
        if path == "/repos/owner/repo/actions/runs/10/artifacts":
            data = archive_bytes(self.report)
            artifact = {
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(), **self.artifact,
            }
            return httpx.Response(200, json={"total_count": 1, "artifacts": [artifact]})
        if path == "/repos/owner/repo/actions/artifacts/60/zip":
            if self.redirect:
                return httpx.Response(302, headers={"Location": "https://untrusted.example.test/private.zip"})
            return httpx.Response(200, content=archive_bytes(self.report))
        raise AssertionError(path)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> tuple[EvidenceWorld, NativeWorkspace]:
    world = EvidenceWorld()
    config = ConnectorConfig(github_app_id="7")
    selection = Selection(resource_ids=["1"], categories=["actions", "checks", "deployments"])
    account = ConnectorAccount(
        id=uuid4(), owner_id="user_test", provider="github", external_account_id="test-user",
        display_name="GitHub", configuration=selection.model_dump(mode="json"),
    )
    connector = GitHubConnector(config, account, ProviderHttp(config, httpx.MockTransport(world.respond)))
    monkeypatch.setattr(OAuthConnector, "access_token", AsyncMock(return_value="user-test-token"))
    monkeypatch.setattr(OAuthConnector, "success", AsyncMock())
    monkeypatch.setattr(connector, "app_jwt", lambda: "test-app-jwt")
    return world, NativeWorkspace(connector, selection)


def evidence_request(**updates: object) -> GitHubEvidenceRequest:
    return GitHubEvidenceRequest.model_validate({
        "repository_id": "1", "branch": "main", "sha": SHA, "run_id": 10, "workflow_id": 20,
        "expected_suites": ["authentication"], "expected_cases": [{"suite": "authentication", "name": "sign in"}],
        "artifact_names": ["test-results"], **updates,
    })


async def test_exact_run_collects_real_cases_and_keeps_read_only_permissions(
    world: tuple[EvidenceWorld, NativeWorkspace],
) -> None:
    provider, workspace = world
    result = await workspace.github_evidence(evidence_request())
    assert result.run.id == 10 and result.run.head_sha == SHA and result.jobs[0].run_attempt == 1
    assert result.coverage.jobs and result.coverage.checks and result.coverage.deployments and result.coverage.artifacts
    assert result.test_report_status == "parsed" and not result.missing_suites and not result.missing_cases
    assert [(case.suite, case.name, case.status) for case in result.test_results] == [
        ("authentication", "sign in", "passed")
    ]
    assert result.deployments[0].statuses[0].state == "success"
    assert all(request.url.host == "api.github.com" for request in provider.requests)
    assert all(request.method == "GET" or request.url.path.endswith("/access_tokens") for request in provider.requests)


@pytest.mark.parametrize("field,value", [
    ("id", 11), ("workflow_id", 21), ("head_sha", "b" * 40), ("head_branch", "other"),
    ("repository", {"id": 2}), ("head_repository", {"id": 2}), ("created_at", "not-a-date"),
])
async def test_run_identity_and_fields_fail_closed(
    world: tuple[EvidenceWorld, NativeWorkspace], field: str, value: object,
) -> None:
    provider, workspace = world
    provider.run[field] = value
    with pytest.raises(ConnectorError, match=r"Workflow run|invalid evidence"):
        await workspace.github_evidence(evidence_request())


@pytest.mark.parametrize("target,field,value", [
    ("job", "run_id", 11), ("job", "head_sha", "b" * 40), ("job", "run_attempt", 2),
    ("check", "head_sha", "b" * 40), ("artifact", "workflow_run", {"id": 11, "head_sha": SHA}),
])
async def test_child_evidence_identity_is_checked(
    world: tuple[EvidenceWorld, NativeWorkspace], target: str, field: str, value: object,
) -> None:
    provider, workspace = world
    getattr(provider, target)[field] = value
    with pytest.raises(ConnectorError):
        await workspace.github_evidence(evidence_request())


async def test_unselected_actions_and_branch_cannot_expand_access(
    world: tuple[EvidenceWorld, NativeWorkspace],
) -> None:
    provider, workspace = world
    workspace.selection = Selection(resource_ids=["1"], categories=["code"])
    with pytest.raises(ConnectorError, match="read-only Actions"):
        await workspace.github_evidence(evidence_request())
    assert not provider.requests
    workspace.selection = Selection(resource_ids=["1"], categories=["actions"])
    with pytest.raises(ConnectorError, match="selected repository branch"):
        await workspace.github_evidence(evidence_request(branch="private"))


async def test_green_run_and_cross_host_artifact_redirect_do_not_prove_tests(
    world: tuple[EvidenceWorld, NativeWorkspace],
) -> None:
    provider, workspace = world
    provider.redirect = True
    result = await workspace.github_evidence(evidence_request())
    assert result.run.conclusion == "success" and result.jobs[0].conclusion == "success"
    assert result.test_report_status == "unavailable" and not result.test_results
    assert result.missing_suites == ["authentication"]
    assert result.missing_cases == [GitHubExpectedCase(suite="authentication", name="sign in")]
    assert all(request.url.host == "api.github.com" for request in provider.requests)


@pytest.mark.parametrize("scenario", ["expired", "digest", "rerun", "missing"])
async def test_unbound_artifacts_never_supply_cases(
    world: tuple[EvidenceWorld, NativeWorkspace], scenario: str,
) -> None:
    provider, workspace = world
    if scenario == "expired":
        provider.artifact["expired"] = True
    if scenario == "digest":
        provider.artifact["digest"] = "sha256:" + "0" * 64
    if scenario == "rerun":
        provider.run["run_attempt"] = 2
    if scenario == "missing":
        provider.artifact["name"] = "different-results"
    result = await workspace.github_evidence(evidence_request())
    assert not result.test_results and result.test_report_status == "unavailable"


@pytest.mark.parametrize("scenario", ["run", "head", "old_sha"])
async def test_changed_or_historical_evidence_is_not_returned(
    world: tuple[EvidenceWorld, NativeWorkspace], scenario: str,
) -> None:
    provider, workspace = world
    provider.change_run = scenario == "run"
    provider.change_head = scenario == "head"
    provider.head = "b" * 40 if scenario == "old_sha" else SHA
    with pytest.raises(ConnectorError, match=r"changed|current selected"):
        await workspace.github_evidence(evidence_request())


async def test_unselected_optional_evidence_and_truncated_jobs_are_explicit(
    world: tuple[EvidenceWorld, NativeWorkspace],
) -> None:
    provider, workspace = world
    provider.truncated_jobs = True
    workspace.selection = Selection(resource_ids=["1"], categories=["actions"])
    result = await workspace.github_evidence(evidence_request())
    assert not result.coverage.jobs and not result.coverage.checks and not result.coverage.deployments
    assert not result.checks and not result.deployments
    assert not any(
        "check-runs" in str(request.url) or "deployments" in str(request.url) for request in provider.requests
    )


async def test_new_evidence_categories_do_not_enter_canonical_inventory(
    world: tuple[EvidenceWorld, NativeWorkspace],
) -> None:
    _, workspace = world
    result = await workspace.connector.initial_sync(workspace.selection, {})
    assert result.complete and [resource.kind for resource in result.resources] == ["repository_metadata"]


@pytest.mark.parametrize("body,expected", [
    (b'<testsuite name="auth"><testcase name="login"><failure/></testcase></testsuite>', "failed"),
    (b'<testsuite name="auth"><testcase name="login"><error/></testcase></testsuite>', "error"),
    (b'<testsuite name="auth"><testcase name="login"><skipped/></testcase></testsuite>', "skipped"),
    (b'<testsuite name="auth"><testcase name="login" status="notrun"/></testsuite>', "skipped"),
])
def test_junit_records_case_outcome_instead_of_workflow_color(body: bytes, expected: str) -> None:
    assert junit_report(body, 1, "report.xml")[0].status == expected


@pytest.mark.parametrize("body", [
    b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><testsuite name="auth">&secret;</testsuite>',
    b'<testsuite name="auth" tests="10"/>',
    b'<testcase name="login"/>',
    b'<testsuite><testcase name="login"/></testsuite>',
    b'<testsuite name="auth"><testcase name="login"/><testcase name="login"/></testsuite>',
    b'<testsuite name="auth" tests="2" failures="1"><testcase name="login"/></testsuite>',
    b'<testsuite name="auth" tests="1" failures="1"><testcase name="login"/></testsuite>',
    b'<testsuite name="auth"><testcase name="login"><flakyFailure/></testcase></testsuite>',
])
def test_unsafe_empty_or_ambiguous_reports_are_rejected(body: bytes) -> None:
    data = archive_bytes(body)
    with pytest.raises((ConnectorError, ValidationError)):
        junit_archive(data, 1, "sha256:" + hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("path", ["../private.xml", "/private.xml", "nested\\private.xml"])
def test_archive_path_traversal_is_rejected_without_extraction(path: str) -> None:
    data = archive_bytes(b'<testsuite name="auth"><testcase name="login"/></testsuite>', path)
    with pytest.raises(ConnectorError, match="unsafe archive"):
        junit_archive(data, 1, "sha256:" + hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("updates", [
    {"sha": "main"}, {"repository_id": "owner/repo"}, {"branch": "../private"}, {"max_pages": 6},
    {"expected_suites": ["auth", "auth"]}, {"artifact_names": ["x"] * 6},
])
def test_evidence_request_rejects_unbounded_or_ambiguous_inputs(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        evidence_request(**updates)
