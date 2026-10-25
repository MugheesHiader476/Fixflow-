"""Real PostgreSQL, parsing and jobs; native GitHub observations are explicitly mocked."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from io import BytesIO
from uuid import UUID

import httpx
import pytest
from pypdf import PdfReader
from sqlalchemy import func, select

from backend.connectors.core import record, records
from backend.connectors.workspace import NativeWorkspace
from backend.db.models import ConnectorAccount, ConnectorCredential, KnowledgeSource
from backend.db.proofos import (
    ProofApproval,
    ProofContract,
    ProofEvidence,
    ProofInvoice,
    ProofJob,
    ProofPackage,
    ProofRequirement,
    ProofVerification,
)
from backend.db.session import get_session_factory
from backend.schemas.connectors import Selection
from backend.schemas.github_evidence import GitHubEvidenceRequest, GitHubEvidenceSnapshot
from backend.schemas.pipeline import digest
from backend.services import proofos as domain
from backend.services.ingestion import ingest_source
from backend.services.proofos_jobs import process_job

pytestmark = pytest.mark.anyio
BASE = "/api/proofos"
SHA = "a" * 40
OWNER = "user_test"
CUSTOMER = "user_customer"


async def post(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    status: int = 200,
    actor: str = OWNER,
) -> dict[str, object]:
    response = await client.post(BASE + path, json=payload or {}, headers={"X-FixFlow-User-Id": actor})
    assert response.status_code == status, response.text
    return record(response.json())


async def upload(client: httpx.AsyncClient, name: str, content: str) -> UUID:
    response = await client.post("/api/documents", files={"file": (name, content.encode(), "text/markdown")})
    assert response.status_code == 202, response.text
    identifier = UUID(str(response.json()["source_id"]))
    await ingest_source(identifier)
    return identifier


async def finish(client: httpx.AsyncClient, job: dict[str, object]) -> dict[str, object]:
    identifier = UUID(str(job["id"]))
    await process_job(identifier)
    response = await client.get(f"{BASE}/jobs/{identifier}")
    assert response.status_code == 200, response.text
    result = record(response.json())
    assert result["status"] == "completed", result
    return record(result["result"])


async def workspace(client: httpx.AsyncClient, project: UUID) -> dict[str, object]:
    response = await client.get(f"{BASE}/projects/{project}")
    assert response.status_code == 200, response.text
    return record(response.json())


@dataclass
class World:
    project: UUID
    contract: UUID
    milestone: UUID
    source: UUID
    account: UUID
    requirements: list[dict[str, object]]
    selection: Selection
    outcome: str = "passed"


@pytest.fixture
async def proof_world(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> World:
    selection = Selection(resource_ids=["42"], branches={"42": "main"}, categories=["actions", "checks", "deployments"])
    async with get_session_factory().begin() as db:
        credential = ConnectorCredential(owner_id=OWNER, ciphertext="synthetic-unused-encrypted-credential")
        db.add(credential)
        await db.flush()
        account = ConnectorAccount(
            owner_id=OWNER,
            provider="github",
            external_account_id="synthetic-github-user",
            display_name="Mock GitHub installation",
            credential_reference=credential.id,
            configuration=selection.model_dump(mode="json"),
        )
        db.add(account)
        await db.flush()
        account_id = account.id

    async def native_repository(self: NativeWorkspace, repository_id: str) -> tuple[dict[str, object], str, str]:
        assert repository_id == "42" and repository_id in self.selection.resource_ids
        return (
            {"id": 42, "full_name": "example/portal", "default_branch": "main"},
            "example/portal",
            "unused-mocked-token",
        )

    monkeypatch.setattr(NativeWorkspace, "github_repository", native_repository)
    project = await post(
        client,
        "/projects",
        {
            "name": "Portal delivery",
            "customer_name": "Example customer",
            "customer_approver_id": CUSTOMER,
            "github_account_id": str(account_id),
            "repository_id": "42",
            "repository_name": "example/portal",
            "branch": "main",
        },
        status=201,
    )
    project_id = UUID(str(project["id"]))
    source = await upload(
        client,
        "contract.md",
        "# Delivery contract\n\nUsers must authenticate with tenant isolation.\n\n"
        "The supplier shall provide a delivery guide.\n",
    )
    extracted = await finish(
        client,
        await post(
            client, f"/projects/{project_id}/contracts", {"source_id": str(source), "title": "Portal SRS"}, status=202
        ),
    )
    contract_id = UUID(str(extracted["contract_id"]))
    requirements = records((await workspace(client, project_id))["requirements"])
    assert len(requirements) == 2
    for requirement in requirements:
        technical = "authenticate" in str(requirement["original_text"])
        criterion: dict[str, object] = {
            "id": "auth" if technical else "guide",
            "description": str(requirement["original_text"]),
            "category": "technical" if technical else "delivery",
            "kind": "ci" if technical else "document",
            "mandatory": True,
        }
        if technical:
            criterion.update({"suite": "authentication", "cases": ["login", "tenant_isolation"], "workflow_id": 7})
        await post(
            client,
            f"/requirements/{requirement['id']}/review",
            {
                "interpretation": str(requirement["original_text"]),
                "acceptance_criteria": [criterion],
                "ambiguities": [],
                "engineering_links": [],
                "approve": True,
            },
        )
    await post(client, f"/contracts/{contract_id}/approve")
    milestone = await post(
        client,
        f"/projects/{project_id}/milestones",
        {
            "contract_id": str(contract_id),
            "code": "M1",
            "title": "Authenticated portal",
            "expected_sha": SHA,
            "amount_minor": 125000,
            "currency": "USD",
            "payment_terms": "Net 30 after authenticated customer acceptance",
        },
        status=201,
    )
    milestone_id = UUID(str(milestone["id"]))
    for requirement in requirements:
        await post(client, f"/requirements/{requirement['id']}/assign", {"milestone_id": str(milestone_id)})
    await post(client, f"/milestones/{milestone_id}/approve")
    world = World(project_id, contract_id, milestone_id, source, account_id, requirements, selection)

    async def native_evidence(self: NativeWorkspace, request: GitHubEvidenceRequest) -> GitHubEvidenceSnapshot:
        assert request.repository_id == "42" and request.sha == SHA and request.workflow_id == 7
        observed = datetime.now(UTC)
        return GitHubEvidenceSnapshot.model_validate(
            {
                "repository_id": "42",
                "repository_name": "example/portal",
                "branch": "main",
                "sha": SHA,
                "observed_at": observed,
                "selection_hash": digest(self.selection.model_dump(mode="json")),
                "run": {
                    "id": request.run_id,
                    "workflow_id": 7,
                    "run_attempt": 1,
                    "head_sha": SHA,
                    "head_branch": "main",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": observed - timedelta(minutes=5),
                    "updated_at": observed - timedelta(minutes=1),
                    "html_url": f"https://github.com/example/portal/actions/runs/{request.run_id}",
                },
                "jobs": [],
                "checks": [],
                "deployments": [],
                "artifacts": [],
                "test_results": [
                    {
                        "suite": "authentication",
                        "name": name,
                        "status": world.outcome if name == "login" else "passed",
                        "artifact_id": 10,
                        "report_path": "junit.xml",
                        "report_sha256": "b" * 64,
                    }
                    for name in ("login", "tenant_isolation")
                ],
                "test_report_status": "parsed",
                "coverage": {"jobs": True, "checks": True, "deployments": True, "artifacts": True},
                "limitations": ["Synthetic native GitHub test observation; no live provider verification"],
            }
        )

    monkeypatch.setattr(NativeWorkspace, "github_evidence", native_evidence)
    return world


async def collect(client: httpx.AsyncClient, world: World, *, run_id: int = 11) -> None:
    await finish(
        client,
        await post(
            client,
            f"/milestones/{world.milestone}/collect",
            {"run_id": run_id, "workflow_id": 7, "artifact_names": ["test-results"]},
            status=202,
        ),
    )


async def delivery(client: httpx.AsyncClient, world: World) -> UUID:
    source = await upload(
        client, "delivery-guide.md", "# Delivery guide\n\nRun the portal and sign in with the authorized account.\n"
    )
    requirement = next(value for value in world.requirements if "guide" in str(value["original_text"]))
    await finish(
        client,
        await post(
            client,
            f"/milestones/{world.milestone}/evidence",
            {
                "source_id": str(source),
                "requirement_ids": [str(requirement["id"])],
                "kind": "document",
                "customer_safe": True,
            },
            status=202,
        ),
    )
    return source


async def verify_world(client: httpx.AsyncClient, world: World) -> dict[str, object]:
    await finish(client, await post(client, f"/milestones/{world.milestone}/verify", status=202))
    return record(records((await workspace(client, world.project))["milestones"])[0]["latest_verification"])


async def make_package(client: httpx.AsyncClient, world: World) -> dict[str, object]:
    result = await finish(client, await post(client, f"/milestones/{world.milestone}/packages", status=202))
    response = await client.get(f"{BASE}/packages/{result['package_id']}")
    assert response.status_code == 200, response.text
    return record(response.json())


async def ready_package(client: httpx.AsyncClient, world: World) -> dict[str, object]:
    await collect(client, world)
    await delivery(client, world)
    await verify_world(client, world)
    return await make_package(client, world)


async def request_customer(client: httpx.AsyncClient, package: dict[str, object]) -> dict[str, object]:
    metadata = record(package["package"])
    await post(
        client,
        f"/packages/{metadata['id']}/review",
        {
            "decision": "approved",
            "expected_hash": metadata["internal_hash"],
            "reason": "Reviewed the exact verification and package",
        },
    )
    return await post(client, f"/packages/{metadata['id']}/request-approval", {"expires_in_hours": 72})


async def test_srs_proposals_preserve_wording_scope_approval_and_contract_immutability(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    state = await workspace(client, proof_world.project)
    assert {value["original_text"] for value in records(state["requirements"])} == {
        "Users must authenticate with tenant isolation.",
        "The supplier shall provide a delivery guide.",
    }
    assert all(record(value["source_location"])["block_id"] for value in records(state["requirements"]))
    requirement = proof_world.requirements[0]
    await post(
        client,
        f"/requirements/{requirement['id']}/review",
        {
            "interpretation": "Changed interpretation",
            "acceptance_criteria": [
                {"id": "replacement", "description": "Changed", "category": "technical", "kind": "manual"}
            ],
            "approve": True,
        },
        status=409,
    )
    await post(
        client, f"/requirements/{requirement['id']}/assign", {"milestone_id": str(proof_world.milestone)}, status=409
    )
    packet = await client.get(f"{BASE}/milestones/{proof_world.milestone}/task-packet")
    assert packet.status_code == 200
    assert packet.json()["execution_authorized"] is False and packet.json()["runner"] == "not_configured"
    assert records((await workspace(client, proof_world.project))["requirements"]) == records(state["requirements"])


async def test_owner_and_cross_project_boundaries_fail_before_private_content(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    assert (
        await client.get(f"{BASE}/projects/{proof_world.project}", headers={"X-FixFlow-User-Id": "user_other"})
    ).status_code == 404
    assert (await client.get(f"{BASE}/projects", headers={"X-FixFlow-User-Id": "user_other"})).json() == []
    assert (await client.get(f"{BASE}/projects", headers={"Authorization": "Bearer invalid"})).status_code == 401
    other = await post(client, "/projects", {"name": "Other project", "customer_name": "Other customer"}, status=201)
    await post(
        client,
        f"/projects/{other['id']}/milestones",
        {"contract_id": str(proof_world.contract), "code": "CROSS", "title": "Cross project"},
        status=404,
    )
    await post(
        client,
        "/projects",
        {"name": "Invalid approval authority", "customer_name": "Customer", "customer_approver_id": OWNER},
        status=422,
    )
    await post(
        client,
        f"/projects/{proof_world.project}/contracts",
        {"source_id": str(proof_world.source), "title": "Foreign contract"},
        status=404,
        actor="user_other",
    )


async def test_scenario_a_failed_auth_missing_delivery_and_discussion_claim_remain_blocked(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    proof_world.outcome = "failed"
    await collect(client, proof_world)
    source = await upload(
        client,
        "discussion-snapshot.md",
        "# Unverified discussion snapshot\n\nA Slack participant says all authentication work is complete.\n",
    )
    auth = next(value for value in proof_world.requirements if "authenticate" in str(value["original_text"]))
    await finish(
        client,
        await post(
            client,
            f"/milestones/{proof_world.milestone}/evidence",
            {
                "source_id": str(source),
                "requirement_ids": [str(auth["id"])],
                "kind": "discussion",
                "customer_safe": False,
            },
            status=202,
        ),
    )
    result = record((await verify_world(client, proof_world))["result"])
    assert result["technical_status"] == "FAILED" and result["delivery_status"] == "UNVERIFIED"
    await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)
    package = record((await make_package(client, proof_world))["package"])
    await post(
        client,
        f"/packages/{package['id']}/review",
        {"decision": "approved", "expected_hash": package["internal_hash"], "reason": "Failures acknowledged"},
    )
    await post(client, f"/packages/{package['id']}/request-approval", {"expires_in_hours": 72}, status=409)


async def test_scenarios_b_and_c_customer_pending_then_actual_acceptance_allows_one_invoice_draft(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    result = record(record(package["customer"])["verification"])
    assert result["technical_status"] == result["delivery_status"] == "PASSED"
    assert record(package["customer"])["customer_acceptance"] == {"status": "pending"}
    await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)
    approval = await request_customer(client, package)
    body = {
        "decision": "accepted",
        "expected_hash": metadata["customer_hash"],
        "reason": "The delivered milestone meets the contract",
    }
    await post(client, f"/approvals/{approval['id']}/decide", body, status=404)
    await post(client, f"/approvals/{approval['id']}/decide", body, actor=CUSTOMER)
    await post(client, f"/approvals/{approval['id']}/decide", body, actor=CUSTOMER, status=409)
    invoice = await post(client, f"/milestones/{proof_world.milestone}/invoice-draft")
    repeated = await post(client, f"/milestones/{proof_world.milestone}/invoice-draft")
    assert invoice["id"] == repeated["id"]
    assert invoice["amount_minor"] == 125000 and invoice["currency"] == "USD"
    assert invoice["issued"] is False and invoice["sent"] is False
    assert invoice["taxes"] is None and invoice["bank_details"] is None
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ProofInvoice)) == 1


async def test_private_package_download_safe_customer_projection_and_exact_review_hash(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    assert "internal_review" in record(package["internal"]) and "internal_review" not in record(package["customer"])
    path = f"{BASE}/packages/{metadata['id']}/download/customer/pdf"
    assert (await client.get(path, headers={"X-FixFlow-User-Id": "user_other"})).status_code == 404
    response = await client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "ProofOS" in "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    await post(
        client,
        f"/packages/{metadata['id']}/review",
        {"decision": "approved", "expected_hash": "0" * 64, "reason": "Wrong bytes"},
        status=409,
    )
    approval = await request_customer(client, package)
    assert (
        await client.get(f"{BASE}/approvals/{approval['id']}", headers={"X-FixFlow-User-Id": OWNER})
    ).status_code == 404
    customer = await client.get(f"{BASE}/approvals/{approval['id']}", headers={"X-FixFlow-User-Id": CUSTOMER})
    assert customer.status_code == 200 and "internal_review" not in customer.json()["package"]
    download = await client.get(
        f"{BASE}/approvals/{approval['id']}/download/json", headers={"X-FixFlow-User-Id": CUSTOMER}
    )
    assert download.status_code == 200 and digest(download.json()) == metadata["customer_hash"]


async def test_customer_expiry_wrong_hash_and_replayed_decisions_are_rejected(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    decision: dict[str, object] = {"decision": "accepted", "expected_hash": "0" * 64, "reason": "Wrong hash"}
    await post(client, f"/approvals/{approval['id']}/decide", decision, actor=CUSTOMER, status=409)
    async with get_session_factory().begin() as db:
        row = await db.get(ProofApproval, UUID(str(approval["id"])))
        assert row
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    decision["expected_hash"] = metadata["customer_hash"]
    await post(client, f"/approvals/{approval['id']}/decide", decision, actor=CUSTOMER, status=409)
    assert (
        await client.get(f"{BASE}/approvals/{approval['id']}", headers={"X-FixFlow-User-Id": CUSTOMER})
    ).status_code == 409


async def test_new_verification_and_package_invalidate_old_customer_decision(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = await ready_package(client, proof_world)
    old = record(package["package"])
    approval = await request_customer(client, package)
    await verify_world(client, proof_world)
    new = record((await make_package(client, proof_world))["package"])
    assert new["version"] == 2 and new["customer_hash"] != old["customer_hash"]
    await post(
        client,
        f"/approvals/{approval['id']}/decide",
        {"decision": "accepted", "expected_hash": old["customer_hash"], "reason": "Stale review"},
        actor=CUSTOMER,
        status=409,
    )
    await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ProofPackage)) == 2


async def test_changed_source_or_revoked_connection_blocks_historical_package_download(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = record((await ready_package(client, proof_world))["package"])
    path = f"{BASE}/packages/{package['id']}/download/customer/json"
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, proof_world.account)
        assert account
        account.status = "disconnected"
    assert (await client.get(path)).status_code == 409
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, proof_world.account)
        assert account
        account.status = "connected"
        source = await db.get(KnowledgeSource, proof_world.source)
        assert source
        source.is_active = False
    assert (await client.get(path)).status_code == 409


async def test_cancelled_queued_job_cannot_publish_and_foreign_owner_cannot_poll_it(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    job = await post(client, f"/milestones/{proof_world.milestone}/verify", status=202)
    assert (
        await client.get(f"{BASE}/jobs/{job['id']}", headers={"X-FixFlow-User-Id": "user_other"})
    ).status_code == 404
    cancelled = await post(client, f"/jobs/{job['id']}/cancel")
    assert cancelled["status"] == "cancelled"
    await process_job(UUID(str(job["id"])))
    async with get_session_factory()() as db:
        row = await db.get(ProofJob, UUID(str(job["id"])))
        assert row and row.status == "cancelled" and row.result == {}
        assert await db.scalar(select(func.count()).select_from(ProofEvidence)) == 0
        assert await db.scalar(select(func.count()).select_from(ProofContract)) == 1


async def test_latest_native_run_can_resolve_failure_without_rewriting_history(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    proof_world.outcome = "failed"
    await collect(client, proof_world)
    await delivery(client, proof_world)
    assert record((await verify_world(client, proof_world))["result"])["technical_status"] == "FAILED"
    proof_world.outcome = "passed"
    await collect(client, proof_world, run_id=12)
    assert record((await verify_world(client, proof_world))["result"])["technical_status"] == "PASSED"
    evidence = records((await workspace(client, proof_world.project))["evidence"])
    native = [record(value["fact"]) for value in evidence if record(value["fact"]).get("kind") == "ci"]
    assert {(value["run_id"], value["status"]) for value in native} == {(11, "failed"), (12, "passed")}


async def test_native_inflight_cancellation_cannot_publish_stale_observations(
    client: httpx.AsyncClient, proof_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await post(
        client, f"/milestones/{proof_world.milestone}/collect", {"run_id": 11, "workflow_id": 7}, status=202
    )
    original = NativeWorkspace.github_evidence

    async def cancel_during_fetch(self: NativeWorkspace, request: GitHubEvidenceRequest) -> GitHubEvidenceSnapshot:
        result = await original(self, request)
        async with get_session_factory().begin() as db:
            row = await db.get(ProofJob, UUID(str(job["id"])))
            assert row and row.status == "running"
            row.status, row.run_token = "cancelled", None
        return result

    monkeypatch.setattr(NativeWorkspace, "github_evidence", cancel_during_fetch)
    await process_job(UUID(str(job["id"])))
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ProofEvidence)) == 0
        row = await db.get(ProofJob, UUID(str(job["id"])))
        assert row and row.status == "cancelled"


async def test_changed_selection_prevents_private_package_download(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = record((await ready_package(client, proof_world))["package"])
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, proof_world.account)
        assert account
        account.configuration = Selection().model_dump(mode="json")
    assert (await client.get(f"{BASE}/packages/{package['id']}/download/customer/json")).status_code == 409


async def test_changed_evidence_source_before_worker_never_registers_snapshot(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    source_id = await upload(client, "versioned-guide.md", "# Guide\n\nDelivery evidence for the selected milestone.\n")
    requirement = next(value for value in proof_world.requirements if "guide" in str(value["original_text"]))
    job = await post(
        client,
        f"/milestones/{proof_world.milestone}/evidence",
        {"source_id": str(source_id), "requirement_ids": [str(requirement["id"])], "kind": "document"},
        status=202,
    )
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, source_id)
        assert source
        source.file_hash = "c" * 64
    await process_job(UUID(str(job["id"])))
    async with get_session_factory()() as db:
        row = await db.get(ProofJob, UUID(str(job["id"])))
        assert row and row.status == "failed"
        assert row.error and "c" * 64 not in row.error
        assert await db.scalar(select(func.count()).select_from(ProofEvidence)) == 0


async def test_invoice_rechecks_evidence_freshness_after_customer_acceptance(
    client: httpx.AsyncClient, proof_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    await post(
        client,
        f"/approvals/{approval['id']}/decide",
        {"decision": "accepted", "expected_hash": metadata["customer_hash"], "reason": "Delivery accepted"},
        actor=CUSTOMER,
    )

    class FutureDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "FutureDatetime":
            return cls.fromtimestamp(datetime.now(tz).timestamp() + timedelta(hours=169).total_seconds(), tz)

    monkeypatch.setattr(domain, "datetime", FutureDatetime)
    response = await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)
    assert "verification" in str(response).lower()


@pytest.mark.parametrize("decision", ["rejected", "rework_requested"])
async def test_customer_rejection_or_rework_never_enables_invoice(
    client: httpx.AsyncClient, proof_world: World, decision: str
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    await post(
        client,
        f"/approvals/{approval['id']}/decide",
        {
            "decision": decision,
            "expected_hash": metadata["customer_hash"],
            "reason": "Additional customer work requested",
        },
        actor=CUSTOMER,
    )
    await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)


async def test_changed_valid_connection_selection_blocks_issued_customer_review_and_reissue(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, proof_world.account)
        assert account and account.status == "connected" and account.credential_reference
        account.configuration = {**account.configuration, "categories": ["code"]}
    await post(client, f"/packages/{metadata['id']}/request-approval", {"expires_in_hours": 72}, status=409)
    await post(
        client,
        f"/approvals/{approval['id']}/decide",
        {"decision": "accepted", "expected_hash": metadata["customer_hash"], "reason": "Stale selected evidence"},
        actor=CUSTOMER,
        status=409,
    )
    async with get_session_factory()() as db:
        row = await db.get(ProofApproval, UUID(str(approval["id"])))
        assert row and row.status == "pending" and row.decided_at is None


async def test_retired_verification_policy_cannot_gain_new_approval_or_invoice_authority(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    async with get_session_factory().begin() as db:
        verification = await db.get(ProofVerification, UUID(str(metadata["verification_id"])))
        assert verification
        verification.result = {**verification.result, "policy_version": "retired-proofos-policy"}
    state = await workspace(client, proof_world.project)
    readiness = record(records(state["milestones"])[0]["readiness"])
    assert readiness["eligible"] is False and "policy" in str(readiness["reasons"]).lower()
    await post(client, f"/packages/{metadata['id']}/request-approval", {"expires_in_hours": 72}, status=409)
    await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)
    await post(
        client,
        f"/approvals/{approval['id']}/decide",
        {"decision": "accepted", "expected_hash": metadata["customer_hash"], "reason": "Old verification policy"},
        actor=CUSTOMER,
        status=409,
    )


@pytest.mark.parametrize("damage", ["criteria", "facts", "result", "input_hash"])
async def test_stored_verification_tampering_blocks_customer_approval_and_invoice(
    client: httpx.AsyncClient, proof_world: World, damage: str
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    async with get_session_factory().begin() as db:
        verification = await db.get(ProofVerification, UUID(str(metadata["verification_id"])))
        assert verification
        if damage == "input_hash":
            verification.input_hash = "f" * 64
        elif damage == "result":
            verification.result = {**verification.result, "technical_status": "FAILED"}
        else:
            entries = records(verification.snapshot[damage])
            field = "description" if damage == "criteria" else "title"
            entries[0] = {**entries[0], field: "TAMPERED_PRIVATE_TEST_VALUE"}
            verification.snapshot = {**verification.snapshot, damage: entries}
    failure = await post(client, f"/packages/{metadata['id']}/request-approval", {"expires_in_hours": 72}, status=409)
    assert "TAMPERED_PRIVATE_TEST_VALUE" not in str(failure)
    await post(client, f"/milestones/{proof_world.milestone}/invoice-draft", status=409)
    await post(
        client,
        f"/approvals/{approval['id']}/decide",
        {"decision": "accepted", "expected_hash": metadata["customer_hash"], "reason": "Tampered verification"},
        actor=CUSTOMER,
        status=409,
    )


async def test_reviewed_amendment_to_same_source_allows_new_scope_and_preserves_frozen_contract(
    client: httpx.AsyncClient, proof_world: World
) -> None:
    original = {str(value["id"]): str(value["original_text"]) for value in proof_world.requirements}
    async with get_session_factory()() as db:
        old_contract = await db.get(ProofContract, proof_world.contract)
        assert old_contract
        original_hash = old_contract.source_hash
    response = await client.post(
        "/api/documents",
        data={"update_source_id": str(proof_world.source), "force": "true"},
        files={
            "file": (
                "contract-amended.md",
                b"# Amended delivery contract\n\n"
                b"Users must authenticate with tenant isolation and reviewed recovery.\n\n"
                b"The supplier shall provide a delivery guide and recovery procedure.\n",
                "text/markdown",
            )
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["source_id"] == str(proof_world.source)
    await ingest_source(proof_world.source)
    extracted = await finish(
        client,
        await post(
            client,
            f"/projects/{proof_world.project}/contracts",
            {"source_id": str(proof_world.source), "title": "Reviewed amendment"},
            status=202,
        ),
    )
    contract_id = UUID(str(extracted["contract_id"]))
    state = await workspace(client, proof_world.project)
    requirements = [value for value in records(state["requirements"]) if value["contract_id"] == str(contract_id)]
    assert len(requirements) == 2
    for requirement in requirements:
        technical = "authenticate" in str(requirement["original_text"])
        criterion: dict[str, object] = {
            "id": "amended-auth" if technical else "amended-guide",
            "description": str(requirement["original_text"]),
            "category": "technical" if technical else "delivery",
            "kind": "ci" if technical else "document",
        }
        if technical:
            criterion.update({"suite": "authentication", "cases": ["login", "tenant_isolation"], "workflow_id": 7})
        await post(
            client,
            f"/requirements/{requirement['id']}/review",
            {
                "interpretation": str(requirement["original_text"]),
                "acceptance_criteria": [criterion],
                "ambiguities": [],
                "approve": True,
            },
        )
    await post(client, f"/contracts/{contract_id}/approve")
    milestone = await post(
        client,
        f"/projects/{proof_world.project}/milestones",
        {
            "contract_id": str(contract_id),
            "code": "M2",
            "title": "Amended delivery",
            "expected_sha": SHA,
        },
        status=201,
    )
    for requirement in requirements:
        await post(client, f"/requirements/{requirement['id']}/assign", {"milestone_id": milestone["id"]})
    approved = await post(client, f"/milestones/{milestone['id']}/approve")
    assert approved["scope_approved"] is True
    old_job = await post(client, f"/milestones/{proof_world.milestone}/verify", status=202)
    await process_job(UUID(str(old_job["id"])))
    old_status = await client.get(f"{BASE}/jobs/{old_job['id']}")
    assert old_status.status_code == 200 and old_status.json()["status"] == "failed"
    await finish(client, await post(client, f"/milestones/{milestone['id']}/verify", status=202))
    async with get_session_factory()() as db:
        frozen = list(
            await db.scalars(select(ProofRequirement).where(ProofRequirement.contract_id == proof_world.contract))
        )
        assert {str(value.id): value.original_text for value in frozen} == original
        old_contract = await db.get(ProofContract, proof_world.contract)
        new_contract = await db.get(ProofContract, contract_id)
        assert old_contract and new_contract
        assert old_contract.source_hash == original_hash and new_contract.source_hash != original_hash
        assert old_contract.revision == 1 and new_contract.revision == 2 and new_contract.source_version == 2
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ProofVerification)
                .where(ProofVerification.milestone_id == proof_world.milestone)
            )
            == 0
        )


@pytest.mark.parametrize("action", ["request", "decide"])
async def test_selection_changed_during_native_preflight_is_rechecked_before_approval_commit(
    client: httpx.AsyncClient, proof_world: World, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    package = await ready_package(client, proof_world)
    metadata = record(package["package"])
    approval = await request_customer(client, package)
    original = NativeWorkspace.github_repository

    async def change_selection_during_preflight(
        self: NativeWorkspace, repository_id: str
    ) -> tuple[dict[str, object], str, str]:
        result = await original(self, repository_id)
        async with get_session_factory().begin() as db:
            account = await db.get(ConnectorAccount, proof_world.account)
            assert account and account.status == "connected"
            account.configuration = {**account.configuration, "categories": ["code"]}
        return result

    monkeypatch.setattr(NativeWorkspace, "github_repository", change_selection_during_preflight)
    if action == "request":
        await post(client, f"/packages/{metadata['id']}/request-approval", {"expires_in_hours": 72}, status=409)
    else:
        await post(
            client,
            f"/approvals/{approval['id']}/decide",
            {"decision": "accepted", "expected_hash": metadata["customer_hash"], "reason": "Selection raced preflight"},
            actor=CUSTOMER,
            status=409,
        )
    async with get_session_factory()() as db:
        row = await db.get(ProofApproval, UUID(str(approval["id"])))
        assert row and row.status == "pending" and row.decided_at is None
