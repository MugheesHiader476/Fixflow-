"""Real PostgreSQL chat-domain boundaries; no live model or provider requests."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update

from backend.config import get_settings
from backend.connectors.core import ConnectorError, record, records
from backend.db.models import AgentRun, ConnectorAccount, ConnectorCredential, KnowledgeSource
from backend.db.proofos import (
    ProofApproval,
    ProofContract,
    ProofEvidence,
    ProofInvoice,
    ProofJob,
    ProofMilestone,
    ProofPackage,
    ProofProject,
    ProofRequirement,
    ProofVerification,
)
from backend.db.session import get_session_factory
from backend.repositories.parsed import parsed_source
from backend.schemas.agent import Material, RunRequest
from backend.schemas.pipeline import digest
from backend.schemas.proofos import ProjectCreate
from backend.schemas.verification import Criterion
from backend.security.policy import POLICY_VERSION, REDACTED
from backend.security.prompt import SYSTEM_PROMPT
from backend.services import agent, proofos_tools
from backend.services.ingestion import ingest_source
from backend.services.proofos_jobs import process_job
from backend.services.verification import verify
from backend.services.workspace_tools import Context, authorize, tool_definitions

pytestmark = pytest.mark.anyio


@dataclass
class Domain:
    source_id: UUID
    project_id: UUID
    milestone_id: UUID
    requirement_id: UUID
    ctx: Context


@pytest.fixture
async def domain(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> Domain:
    monkeypatch.setenv("GROK__API_KEY", "dummy-unit-test-key")
    monkeypatch.setenv("GROK__MODEL", "proofos-test")
    get_settings.cache_clear()
    response = await client.post(
        "/api/documents",
        data={
            "content": "The authentication feature must pass its mandatory automated tests.",
            "value": "SRS",
        },
    )
    assert response.status_code == 202, response.text
    source_id = UUID(response.json()["id"])
    await ingest_source(source_id)
    request = RunRequest(question="Check milestone readiness.")
    scope: dict[str, object] = {"accounts": [], "policy_version": POLICY_VERSION}
    async with get_session_factory().begin() as db:
        parsed = await parsed_source(db, source_id, "user_test")
        assert parsed
        project = ProofProject(
            owner_id="user_test",
            name="Customer Website",
            customer_name="Fictional Customer",
            configuration=ProjectCreate(name="Customer Website", customer_name="Fictional Customer").model_dump(
                mode="json"
            ),
        )
        db.add(project)
        await db.flush()
        contract = ProofContract(
            owner_id="user_test",
            project_id=project.id,
            source_id=source_id,
            title="Contract",
            revision=1,
            source_hash=parsed.canonical.source.sha256,
            source_version=1,
            state="APPROVED",
            snapshot={"source": parsed.canonical.source.model_dump(mode="json")},
        )
        db.add(contract)
        await db.flush()
        criterion = Criterion(
            id="AUTH", description="Mandatory authentication tests pass", category="technical", kind="manual"
        )
        specification = {
            "criteria": [criterion.model_dump(mode="json")],
            "amount_minor": 1_000_000,
            "currency": "USD",
            "payment_terms": "Net 30",
        }
        milestone = ProofMilestone(
            owner_id="user_test",
            project_id=project.id,
            contract_id=contract.id,
            code="M-02",
            title="Customer website",
            specification=specification,
            scope_approved=True,
            scope_hash=digest(specification),
        )
        db.add(milestone)
        await db.flush()
        requirement = ProofRequirement(
            owner_id="user_test",
            project_id=project.id,
            contract_id=contract.id,
            milestone_id=milestone.id,
            stable_id="REQ-001",
            original_text="Authentication must pass mandatory tests.",
            proposal={
                "interpretation": "Authenticate customers",
                "acceptance_criteria": [criterion.model_dump(mode="json")],
                "engineering_links": [],
                "ambiguities": [],
            },
            state="APPROVED",
        )
        run = AgentRun(
            owner_id="user_test",
            request=request.model_dump(mode="json"),
            scope=scope,
            model="proofos-test",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        )
        db.add_all([requirement, run])
        await db.flush()
        return Domain(
            source_id, project.id, milestone.id, requirement.id, Context("user_test", run.id, request, scope, [])
        )


async def call(domain: Domain, name: str, **arguments: object) -> dict[str, object]:
    return await agent.dispatch(domain.ctx, {"name": name, "arguments": json.dumps(arguments)})


def observation(result: dict[str, object]) -> dict[str, object]:
    return record(json.loads(str(records(result["materials"])[0]["text"])))


def job_id(result: dict[str, object]) -> UUID:
    return UUID(str(record(observation(result)["job"])["id"]))


def test_tools_are_classified_without_approval_or_invoice_authority() -> None:
    names = {str(tool["name"]) for tool in tool_definitions()}
    assert set(proofos_tools.PROOF_TOOL_MODELS) <= names
    assert len(proofos_tools.PROOF_TOOL_MODELS) == 13
    assert set(proofos_tools.PROOF_TOOL_KINDS.values()) == {"computational", "read_only"}
    assert not names.intersection(
        {"approve_contract", "approve_milestone", "review_package", "decide", "invoice_draft"}
    )
    assert "draft_email" not in names


async def test_read_tools_return_persisted_statuses_and_traceability(domain: Domain) -> None:
    projects = await call(domain, "list_projects")
    assert domain.project_id.hex in str(projects).replace("-", "")
    milestones = observation(await call(domain, "list_milestones", project_id=str(domain.project_id)))
    assert records(milestones["milestones"])[0]["code"] == "M-02"
    detail = observation(await call(domain, "get_milestone", milestone_id=str(domain.milestone_id)))
    assert record(detail["milestone"])["latest_verification"] is None
    trace = observation(await call(domain, "get_requirement_traceability", requirement_id=str(domain.requirement_id)))
    assert record(trace["requirement"])["original_text"] == "Authentication must pass mandatory tests."
    missing = observation(await call(domain, "list_missing_evidence", milestone_id=str(domain.milestone_id)))
    assert missing["verification_required"] is True
    readiness = observation(await call(domain, "get_invoice_readiness", milestone_id=str(domain.milestone_id)))
    assert record(readiness["readiness"])["eligible"] is False and readiness["invoice_created"] is False


async def test_extraction_is_a_durable_proposal_job_with_origin_scope(domain: Domain) -> None:
    result = await call(
        domain,
        "extract_srs_requirements",
        project_id=str(domain.project_id),
        source_id=str(domain.source_id),
        title="New SRS revision",
    )
    identifier = job_id(result)
    async with get_session_factory()() as db:
        job = await db.get(ProofJob, identifier)
        assert job and job.status == "queued"
        assert job.payload["origin_agent_run_id"] == str(domain.ctx.run_id)
        assert job.payload["origin_accounts"] == []
    await process_job(identifier)
    async with get_session_factory()() as db:
        job = await db.get(ProofJob, identifier)
        assert job and job.status == "completed", job.error if job else None
        contract = await db.get(ProofContract, UUID(str(job.result["contract_id"])))
        assert contract and contract.state == "DRAFT"
        requirements = list(
            await db.scalars(select(ProofRequirement).where(ProofRequirement.contract_id == contract.id))
        )
        assert requirements and all(row.state == "NEEDS_REVIEW" for row in requirements)


async def test_verification_job_returns_the_actual_deterministic_result(domain: Domain) -> None:
    result = await call(domain, "verify_milestone", milestone_id=str(domain.milestone_id))
    identifier = job_id(result)
    await process_job(identifier)
    actual = observation(await call(domain, "get_verification_results", milestone_id=str(domain.milestone_id)))
    verification = record(record(actual["verification"])["result"])
    assert verification["technical_status"] == "UNVERIFIED"
    assert records(verification["checks"])[0]["status"] == "UNVERIFIED"
    status = observation(await call(domain, "get_package_status", job_id=str(identifier)))
    assert record(status["job"])["status"] == "completed" and status["delivered"] is False


async def test_cancelled_origin_cannot_run_queued_domain_work(domain: Domain) -> None:
    identifier = job_id(await call(domain, "verify_milestone", milestone_id=str(domain.milestone_id)))
    async with get_session_factory().begin() as db:
        await db.execute(update(AgentRun).where(AgentRun.id == domain.ctx.run_id).values(status="cancelled"))
    await process_job(identifier)
    async with get_session_factory()() as db:
        job = await db.get(ProofJob, identifier)
        assert job and job.status == "failed"
        assert await db.scalar(select(func.count()).select_from(ProofVerification)) == 0


async def test_package_tool_runs_after_chat_finishes_and_records_no_acceptance(domain: Domain) -> None:
    verification_job = job_id(await call(domain, "verify_milestone", milestone_id=str(domain.milestone_id)))
    await process_job(verification_job)
    package_job = job_id(await call(domain, "prepare_acceptance_package", milestone_id=str(domain.milestone_id)))
    async with get_session_factory().begin() as db:
        await db.execute(update(AgentRun).where(AgentRun.id == domain.ctx.run_id).values(status="completed"))
    await process_job(package_job)
    async with get_session_factory()() as db:
        job = await db.get(ProofJob, package_job)
        assert job and job.status == "completed", job.error if job else None
        package_id = str(job.result["package_id"])
    result = observation(await call(domain, "get_package_status", package_id=package_id))
    assert record(result["package"])["customer_status"] == "pending"
    assert record(result["package"])["internal_review"] == "pending"


async def test_source_collection_remains_internal_and_cannot_mark_customer_safe(domain: Domain) -> None:
    result = await call(
        domain,
        "collect_milestone_evidence",
        milestone_id=str(domain.milestone_id),
        source_id=str(domain.source_id),
        requirement_ids=[str(domain.requirement_id)],
        kind="discussion",
    )
    async with get_session_factory()() as db:
        job = await db.get(ProofJob, job_id(result))
        assert job and job.kind == "source_evidence" and job.payload["customer_safe"] is False
    with pytest.raises(ValueError):
        await call(
            domain,
            "collect_milestone_evidence",
            milestone_id=str(domain.milestone_id),
            source_id=str(domain.source_id),
            requirement_ids=[str(domain.requirement_id)],
            kind="document",
            customer_safe=True,
        )


async def test_foreign_project_and_unoffered_approval_mutations_are_rejected(domain: Domain) -> None:
    other = replace(domain, ctx=replace(domain.ctx, owner="user_other"))
    with pytest.raises(HTTPException) as error:
        await call(other, "get_milestone", milestone_id=str(domain.milestone_id))
    assert error.value.status_code == 404
    with pytest.raises(ValueError, match="Unknown workspace tool"):
        await call(domain, "approve_contract", id=str(domain.project_id))


async def test_project_connector_must_be_inside_captured_chat_scope(domain: Domain) -> None:
    async with get_session_factory().begin() as db:
        credential = ConnectorCredential(owner_id="user_test", ciphertext="mock-encrypted-token")
        db.add(credential)
        await db.flush()
        account = ConnectorAccount(
            owner_id="user_test",
            provider="github",
            external_account_id="github-owner",
            display_name="GitHub",
            credential_reference=credential.id,
            configuration={"resource_ids": ["123"]},
        )
        db.add(account)
        await db.flush()
        project = await db.get(ProofProject, domain.project_id)
        assert project
        project.configuration = {**project.configuration, "github_account_id": str(account.id), "repository_id": "123"}
    with pytest.raises(ConnectorError, match="captured connections"):
        await call(domain, "get_milestone", milestone_id=str(domain.milestone_id))


async def test_source_connector_cannot_bypass_captured_scope(domain: Domain) -> None:
    async with get_session_factory().begin() as db:
        credential = ConnectorCredential(owner_id="user_test", ciphertext="mock-encrypted-token")
        db.add(credential)
        await db.flush()
        account = ConnectorAccount(
            owner_id="user_test",
            provider="google_drive",
            external_account_id="drive-owner",
            display_name="Drive",
            credential_reference=credential.id,
            configuration={"resource_ids": ["folder:private"]},
        )
        db.add(account)
        await db.flush()
        source = await db.get(KnowledgeSource, domain.source_id)
        assert source
        source.connector_account_id = account.id
    with pytest.raises(ConnectorError, match="captured connections"):
        await call(domain, "get_milestone", milestone_id=str(domain.milestone_id))


async def test_material_reauthorization_blocks_changed_contract_versions(domain: Domain) -> None:
    result = await call(domain, "get_milestone", milestone_id=str(domain.milestone_id))
    ctx = replace(domain.ctx, materials=[Material.model_validate(value) for value in records(result["materials"])])
    async with get_session_factory().begin() as db:
        source = await db.get(KnowledgeSource, domain.source_id)
        assert source
        source.file_hash = "b" * 64
    with pytest.raises(ConnectorError, match="source identity changed"):
        await authorize(ctx)


async def test_final_authorization_rechecks_native_evidence(domain: Domain, monkeypatch: pytest.MonkeyPatch) -> None:
    async with get_session_factory().begin() as db:
        db.add(
            ProofEvidence(
                owner_id="user_test",
                project_id=domain.project_id,
                milestone_id=domain.milestone_id,
                identity_hash="a" * 64,
                authority={},
                fact={"requirement_ids": ["AUTH"]},
            )
        )
    check = AsyncMock()
    monkeypatch.setattr(proofos_tools, "evidence_access", check)
    result = await call(domain, "get_milestone", milestone_id=str(domain.milestone_id))
    check.reset_mock()
    await authorize(
        replace(domain.ctx, materials=[Material.model_validate(value) for value in records(result["materials"])]),
        native=True,
    )
    check.assert_awaited_once()
    assert check.await_args and check.await_args.kwargs["native"] is True


async def test_material_reauthorization_rejects_a_new_verification(domain: Domain) -> None:
    observation_result = await call(domain, "get_milestone", milestone_id=str(domain.milestone_id))
    ctx = replace(
        domain.ctx, materials=[Material.model_validate(value) for value in records(observation_result["materials"])]
    )
    result = verify([], [], approved=True, now=datetime.now(UTC))
    async with get_session_factory().begin() as db:
        db.add(
            ProofVerification(
                owner_id="user_test",
                project_id=domain.project_id,
                milestone_id=domain.milestone_id,
                input_hash=result.input_hash,
                snapshot={"criteria": [], "facts": [], "evidence_ids": [], "evidence_hash": digest([])},
                result=result.model_dump(mode="json"),
            )
        )
    with pytest.raises(ConnectorError, match="scope or evidence changed"):
        await authorize(ctx)


async def test_approval_proposal_creates_no_invitation_or_invoice(domain: Domain) -> None:
    async with get_session_factory().begin() as db:
        result = verify([], [], approved=True, now=datetime.now(UTC))
        verification = ProofVerification(
            owner_id="user_test",
            project_id=domain.project_id,
            milestone_id=domain.milestone_id,
            input_hash=result.input_hash,
            snapshot={"criteria": [], "facts": [], "evidence_ids": [], "evidence_hash": digest([])},
            result=result.model_dump(mode="json"),
        )
        db.add(verification)
        await db.flush()
        package = ProofPackage(
            owner_id="user_test",
            project_id=domain.project_id,
            milestone_id=domain.milestone_id,
            verification_id=verification.id,
            version=1,
            internal_hash="b" * 64,
            customer_hash="c" * 64,
            internal={},
            customer={},
        )
        db.add(package)
        await db.flush()
        package_id = package.id
    data = observation(await call(domain, "prepare_approval_request", package_id=str(package_id)))
    assert data["eligible"] is False and data["created_request"] is False and data["delivered"] is False
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ProofApproval)) == 0
        assert await db.scalar(select(func.count()).select_from(ProofInvoice)) == 0


async def test_existing_worker_redacts_domain_material_without_mutating_original(
    domain: Domain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "xai-" + "a1B2" * 10
    async with get_session_factory().begin() as db:
        requirement = await db.get(ProofRequirement, domain.requirement_id)
        run = await db.get(AgentRun, domain.ctx.run_id)
        assert requirement and run
        requirement.proposal = {**requirement.proposal, "interpretation": "Release note api_key=" + secret}
        run.pending_calls = [
            {
                "name": "get_requirement_traceability",
                "call_id": "proofos-local-tool",
                "arguments": json.dumps({"requirement_id": str(domain.requirement_id)}),
            }
        ]
    model = AsyncMock()
    monkeypatch.setattr("backend.services.grok.respond", model)
    await agent.process_step(domain.ctx.run_id)
    model.assert_not_awaited()
    async with get_session_factory()() as db:
        run = await db.get(AgentRun, domain.ctx.run_id)
        requirement = await db.get(ProofRequirement, domain.requirement_id)
        assert run and run.status == "queued" and run.materials
        serialized = json.dumps([run.materials, run.messages])
        assert secret not in serialized and REDACTED in serialized
        assert requirement and secret in str(requirement.proposal)
