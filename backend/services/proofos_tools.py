"""Bounded chat tools for domain reads and local jobs; approvals stay in explicit UI controls."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.core import ConnectorError, records, strings
from backend.db.models import KnowledgeSource
from backend.db.proofos import (
    ProofApproval,
    ProofContract,
    ProofEvidence,
    ProofJob,
    ProofMilestone,
    ProofPackage,
    ProofProject,
    ProofRequirement,
)
from backend.db.session import get_session_factory
from backend.repositories.parsed import parsed_source
from backend.schemas.agent import Material
from backend.schemas.pipeline import Contract, digest
from backend.schemas.proofos import ContractCreate, ExtractionArgs
from backend.services import proofos as service
from backend.services.proofos_access import account_access, contract_current, evidence_access, owned, project_access

if TYPE_CHECKING:
    from backend.services.workspace_tools import Context


class ProjectArgs(Contract):
    project_id: UUID
    limit: int = Field(default=10, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=1000)


class ProjectListArgs(Contract):
    limit: int = Field(default=5, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=1000)


class MilestoneArgs(Contract):
    milestone_id: UUID


class RequirementArgs(Contract):
    requirement_id: UUID


class CollectArgs(MilestoneArgs):
    run_id: int | None = Field(default=None, gt=0)
    workflow_id: int | None = Field(default=None, gt=0)
    artifact_names: list[str] = Field(default_factory=list, max_length=5)
    source_id: UUID | None = None
    requirement_ids: list[UUID] = Field(default_factory=list, max_length=100)
    kind: Literal["document", "discussion"] | None = None

    @model_validator(mode="after")
    def one_evidence_operation(self) -> "CollectArgs":
        if self.source_id:
            if not self.requirement_ids or not self.kind or self.run_id or self.workflow_id or self.artifact_names:
                raise ValueError("Source evidence needs requirement IDs and kind without GitHub run arguments")
        elif not self.run_id or not self.workflow_id or self.requirement_ids or self.kind:
            raise ValueError("GitHub evidence needs an exact run and workflow ID")
        if any(not name.strip() or len(name) > 300 for name in self.artifact_names):
            raise ValueError("Artifact names must be bounded and nonblank")
        return self


class PackageArgs(Contract):
    package_id: UUID


class PackageStatusArgs(Contract):
    package_id: UUID | None = None
    job_id: UUID | None = None

    @model_validator(mode="after")
    def one_identifier(self) -> "PackageStatusArgs":
        if (self.package_id is None) == (self.job_id is None):
            raise ValueError("Choose one persisted package or job ID")
        return self


PROOF_TOOL_MODELS: dict[str, tuple[type[BaseModel], str]] = {
    "list_projects": (ProjectListArgs, "Read-only. List a bounded page of authorized ProofOS projects and IDs."),
    "list_milestones": (ProjectArgs, "Read-only. List a project's approved scope and persisted milestone statuses."),
    "get_milestone": (MilestoneArgs, "Read-only. Inspect scope, latest verification and invoice prerequisites."),
    "extract_srs_requirements": (
        ExtractionArgs,
        "Computational. Queue canonical SRS requirement proposals for owner review. This never approves a contract.",
    ),
    "get_requirement_traceability": (
        RequirementArgs,
        "Read-only. Inspect an original requirement, reviewed criteria, engineering links and associated evidence.",
    ),
    "collect_milestone_evidence": (
        CollectArgs,
        "Computational. Queue exact GitHub run/workflow evidence or map an authorized source to requirements. "
        "Discussion cannot establish acceptance. Source evidence remains internal-only.",
    ),
    "get_verification_results": (
        MilestoneArgs,
        "Read-only. Read the latest persisted deterministic verification, timestamp and policy version.",
    ),
    "verify_milestone": (
        MilestoneArgs,
        "Computational. Queue deterministic verification of approved scope. Never infer or override its outcome.",
    ),
    "list_missing_evidence": (
        MilestoneArgs,
        "Read-only. Show persisted failed, blocked and unverified criteria and separate approval prerequisites.",
    ),
    "prepare_acceptance_package": (
        MilestoneArgs,
        "Computational. Queue a versioned local acceptance package from current verification; it grants no approval.",
    ),
    "get_package_status": (
        PackageStatusArgs,
        "Read-only. Read a persisted local job or package status. Queued does not mean generated or delivered.",
    ),
    "prepare_approval_request": (
        PackageArgs,
        "Read-only. Explain local customer-review eligibility and the owner's required UI action. "
        "Does not create an invitation, notify anyone or record an approval.",
    ),
    "get_invoice_readiness": (
        MilestoneArgs,
        "Read-only. Read contractual invoice prerequisites. Does not create, issue, send or pay an invoice.",
    ),
}
COMPUTATIONAL_TOOLS = frozenset(
    {"extract_srs_requirements", "collect_milestone_evidence", "verify_milestone", "prepare_acceptance_package"}
)
PROOF_TOOL_KINDS = {name: "computational" if name in COMPUTATIONAL_TOOLS else "read_only" for name in PROOF_TOOL_MODELS}


async def captured_account(db: AsyncSession, ctx: "Context", identifier: UUID) -> None:
    captured = next((item for item in ctx.accounts() if item.get("id") == str(identifier)), None)
    account = await account_access(db, ctx.owner, identifier)
    if captured is None or digest(account.configuration) != captured.get("selection_hash"):
        raise ConnectorError("inaccessible", "ProofOS evidence is outside this chat's captured connections")


async def source_authority(db: AsyncSession, ctx: "Context", identifier: UUID) -> str:
    try:
        parsed = await parsed_source(db, identifier, ctx.owner)
    except ValueError as error:
        raise ConnectorError("inaccessible", "Canonical ProofOS source identity changed") from error
    source = await db.get(KnowledgeSource, identifier) if parsed else None
    if parsed is None or source is None:
        raise ConnectorError("inaccessible", "An authorized parsed ProofOS source is unavailable")
    if source.connector_account_id:
        await captured_account(db, ctx, source.connector_account_id)
    return parsed.canonical.source.sha256


async def project_reference(
    db: AsyncSession, ctx: "Context", project: ProofProject, *, native: bool = False
) -> dict[str, object]:
    await project_access(db, project, current=True)
    configured = project.configuration.get("github_account_id")
    if configured:
        await captured_account(db, ctx, UUID(str(configured)))
    contracts = list(
        await db.scalars(
            select(ProofContract)
            .where(ProofContract.project_id == project.id, ProofContract.owner_id == ctx.owner)
            .order_by(ProofContract.id)
            .limit(101)
        )
    )
    if len(contracts) > 100:
        raise ConnectorError("too_large", "Project contract history exceeds the bounded chat view")
    current_hashes: dict[str, str] = {}
    for contract in contracts:
        if not contract.source_id:
            raise ConnectorError("inaccessible", "Contract source access is unavailable")
        current_hashes[str(contract.source_id)] = await source_authority(db, ctx, contract.source_id)
    evidence = list(
        await db.scalars(
            select(ProofEvidence)
            .where(ProofEvidence.project_id == project.id, ProofEvidence.owner_id == ctx.owner)
            .order_by(ProofEvidence.id)
            .limit(501)
        )
    )
    if len(evidence) > 500:
        raise ConnectorError("too_large", "Project evidence exceeds the bounded chat view")
    for item in evidence:
        if item.account_id:
            await captured_account(db, ctx, item.account_id)
        await evidence_access(db, item, native=native, current=False)
        if item.source_id and str(item.source_id) not in current_hashes:
            current_hashes[str(item.source_id)] = await source_authority(db, ctx, item.source_id)
    requirements = list(
        await db.scalars(
            select(ProofRequirement)
            .where(ProofRequirement.project_id == project.id, ProofRequirement.owner_id == ctx.owner)
            .order_by(ProofRequirement.id)
            .limit(501)
        )
    )
    milestones = list(
        await db.scalars(
            select(ProofMilestone)
            .where(ProofMilestone.project_id == project.id, ProofMilestone.owner_id == ctx.owner)
            .order_by(ProofMilestone.id)
            .limit(101)
        )
    )
    if len(requirements) > 500 or len(milestones) > 100:
        raise ConnectorError("too_large", "Project scope exceeds the bounded chat view")
    delivery_state = []
    for milestone in milestones:
        verification = await service.latest_verification(db, milestone)
        package = await service.latest_package(db, milestone)
        package_state = await service.package_view(db, package) if package else {}
        delivery_state.append(
            {
                "milestone_id": str(milestone.id),
                "verification_id": str(verification.id) if verification else None,
                "verification_hash": digest([verification.snapshot, verification.result]) if verification else None,
                "package_id": str(package.id) if package else None,
                "package_hash": package.customer_hash if package else None,
                "internal_review": package_state.get("internal_review"),
                "customer_status": package_state.get("customer_status"),
                "readiness": (await service.readiness(db, milestone)).model_dump(mode="json"),
            }
        )
    snapshot = {
        "project": [project.name, project.customer_name, project.configuration],
        "contracts": [[str(row.id), row.source_hash, row.state] for row in contracts],
        "current_source_hashes": current_hashes,
        "requirements": [[str(row.id), row.state, str(row.milestone_id), digest(row.proposal)] for row in requirements],
        "milestones": [[str(row.id), row.scope_approved, digest(row.specification)] for row in milestones],
        "evidence": [[str(row.id), row.identity_hash, row.authority] for row in evidence],
        "delivery_state": delivery_state,
    }
    return {"proofos_project_id": str(project.id), "proofos_snapshot_hash": digest(snapshot)}


async def authorize_reference(db: AsyncSession, ctx: "Context", reference: dict[str, object], *, native: bool) -> None:
    if not reference.get("proofos_project_id"):
        return
    try:
        project = await owned(db, ProofProject, UUID(str(reference["proofos_project_id"])))
        current = await project_reference(db, ctx, project, native=native)
        if current["proofos_snapshot_hash"] != reference.get("proofos_snapshot_hash"):
            raise ConnectorError("inaccessible", "ProofOS scope or evidence changed; ask for a fresh observation")
    except HTTPException as error:
        raise ConnectorError("inaccessible", "ProofOS project access or evidence changed") from error


def origin(ctx: "Context") -> dict[str, object]:
    return {"origin_agent_run_id": str(ctx.run_id), "origin_accounts": ctx.accounts()}


async def milestone_project(
    db: AsyncSession, ctx: "Context", identifier: UUID
) -> tuple[ProofMilestone, ProofProject, dict[str, object]]:
    milestone = await owned(db, ProofMilestone, identifier)
    project = await owned(db, ProofProject, milestone.project_id)
    reference = await project_reference(db, ctx, project)
    await contract_current(db, milestone.contract_id)
    return milestone, project, reference


async def approval_proposal(db: AsyncSession, ctx: "Context", package: ProofPackage) -> dict[str, object]:
    milestone = await owned(db, ProofMilestone, package.milestone_id)
    project = await owned(db, ProofProject, package.project_id)
    await contract_current(db, milestone.contract_id)
    latest = await service.latest_package(db, milestone)
    verification = await service.latest_verification(db, milestone)
    internal = await db.scalar(
        select(ProofApproval).where(
            ProofApproval.package_id == package.id,
            ProofApproval.kind == "internal",
            ProofApproval.status == "approved",
            ProofApproval.package_hash == package.internal_hash,
        )
    )
    reasons = []
    if (
        not project.configuration.get("customer_approver_id")
        or project.configuration.get("customer_approver_id") == ctx.owner
    ):
        reasons.append("Configure a separate authenticated customer acceptance authority.")
    if internal is None:
        reasons.append("The owner must approve this exact internal package in the UI.")
    if latest is None or latest.id != package.id or verification is None or package.verification_id != verification.id:
        reasons.append("Generate a package from the latest verification.")
    current = service.current_result(verification) if verification else None
    if current is None or any(
        status not in {"PASSED", "NOT_APPLICABLE"} for status in (current.technical_status, current.delivery_status)
    ):
        reasons.append("Mandatory technical and delivery checks must pass.")
    evidence = list(
        await db.scalars(
            select(ProofEvidence)
            .where(ProofEvidence.milestone_id == milestone.id, ProofEvidence.owner_id == ctx.owner)
            .order_by(ProofEvidence.created_at, ProofEvidence.id)
            .limit(501)
        )
    )
    if verification and verification.snapshot.get("evidence_hash") != digest([row.identity_hash for row in evidence]):
        reasons.append("New evidence requires a fresh verification and package.")
    return {
        "package_id": str(package.id),
        "eligible": not reasons,
        "reasons": reasons,
        "status": "local_proposal",
        "created_request": False,
        "delivered": False,
        "required_action": "The owner must use the package's explicit customer review control in ProofOS.",
    }


async def dispatch(
    ctx: "Context",
    name: str,
    args: BaseModel,
    material: Callable[[str, str, dict[str, object]], Material],
) -> dict[str, object]:
    if name not in PROOF_TOOL_MODELS:
        raise ValueError("Unknown ProofOS tool")
    async with get_session_factory()() as db:
        db.info["owner_id"] = ctx.owner
        if isinstance(args, ProjectListArgs):
            projects = list(
                await db.scalars(
                    select(ProofProject)
                    .where(ProofProject.owner_id == ctx.owner)
                    .order_by(ProofProject.created_at.desc(), ProofProject.id)
                    .offset(args.offset)
                    .limit(args.limit)
                )
            )
            materials = []
            for project in projects:
                try:
                    reference = await project_reference(db, ctx, project)
                except (HTTPException, ConnectorError):
                    continue
                materials.append(
                    material(
                        json.dumps({"project": service.project_view(project)}, default=str),
                        "ProofOS project",
                        reference,
                    ).model_dump(mode="json")
                )
            return {
                "materials": materials,
                "classification": "read_only",
                "coverage": "Bounded authorized page",
                "next_offset": args.offset + args.limit if len(projects) == args.limit else None,
            }
        data: dict[str, object]
        if isinstance(args, ExtractionArgs):
            project = await owned(db, ProofProject, args.project_id)
            reference = await project_reference(db, ctx, project)
            await source_authority(db, ctx, args.source_id)
            job = await service.extract(
                db, project.id, ContractCreate(source_id=args.source_id, title=args.title), origin=origin(ctx)
            )
            data = {"job": service.job_view(job), "approved": False, "status": "proposals_queued"}
        elif isinstance(args, ProjectArgs):
            project = await owned(db, ProofProject, args.project_id)
            reference = await project_reference(db, ctx, project)
            rows = list(
                await db.scalars(
                    select(ProofMilestone)
                    .where(ProofMilestone.project_id == project.id, ProofMilestone.owner_id == ctx.owner)
                    .order_by(ProofMilestone.created_at, ProofMilestone.id)
                    .offset(args.offset)
                    .limit(args.limit)
                )
            )
            data = {
                "coverage": "Bounded milestone page",
                "milestones": [await service.milestone_view(db, row) for row in rows],
                "next_offset": args.offset + args.limit if len(rows) == args.limit else None,
            }
        elif isinstance(args, RequirementArgs):
            requirement = await owned(db, ProofRequirement, args.requirement_id)
            project = await owned(db, ProofProject, requirement.project_id)
            reference = await project_reference(db, ctx, project)
            criteria = {str(item["id"]) for item in records(requirement.proposal.get("acceptance_criteria", []))}
            evidence = (
                list(
                    await db.scalars(
                        select(ProofEvidence)
                        .where(
                            ProofEvidence.project_id == project.id,
                            ProofEvidence.owner_id == ctx.owner,
                            ProofEvidence.milestone_id == requirement.milestone_id,
                        )
                        .order_by(ProofEvidence.created_at.desc())
                        .limit(100)
                    )
                )
                if requirement.milestone_id
                else []
            )
            data = {
                "requirement": service.requirement_view(requirement),
                "evidence": [
                    row.fact for row in evidence if criteria.intersection(strings(row.fact.get("requirement_ids", [])))
                ],
                "coverage": "Latest 100 evidence records",
            }
        elif isinstance(args, PackageStatusArgs):
            row: ProofPackage | ProofJob
            if args.package_id:
                row = await owned(db, ProofPackage, args.package_id)
            elif args.job_id:
                row = await owned(db, ProofJob, args.job_id)
            else:
                raise ValueError("A package or job identity is required")
            project = await owned(db, ProofProject, row.project_id)
            reference = await project_reference(db, ctx, project)
            data = (
                {"package": await service.package_view(db, row)}
                if isinstance(row, ProofPackage)
                else {"job": service.job_view(row), "delivered": False}
            )
        elif isinstance(args, PackageArgs):
            package = await owned(db, ProofPackage, args.package_id)
            project = await owned(db, ProofProject, package.project_id)
            reference = await project_reference(db, ctx, project)
            data = await approval_proposal(db, ctx, package)
        elif isinstance(args, MilestoneArgs):
            milestone, project, reference = await milestone_project(db, ctx, args.milestone_id)
            if name == "get_milestone":
                verification = await service.latest_verification(db, milestone)
                data = {
                    "milestone": await service.milestone_view(db, milestone),
                    "current_verification": service.current_result(verification).model_dump(mode="json")
                    if verification
                    else None,
                }
            elif name == "get_invoice_readiness":
                data = {
                    "readiness": (await service.readiness(db, milestone)).model_dump(mode="json"),
                    "invoice_created": False,
                    "issued": False,
                    "sent": False,
                }
            elif name in {"get_verification_results", "list_missing_evidence"}:
                verification = await service.latest_verification(db, milestone)
                current = service.current_result(verification) if verification else None
                if name == "get_verification_results":
                    data = {
                        "verification": service.verification_view(verification) if verification else None,
                        "current_verification": current.model_dump(mode="json") if current else None,
                    }
                else:
                    data = {
                        "verification_id": str(verification.id) if verification else None,
                        "checks": [
                            item.model_dump(mode="json")
                            for item in current.checks
                            if item.status not in {"PASSED", "NOT_APPLICABLE"}
                        ]
                        if current
                        else [],
                        "verification_required": verification is None,
                        "invoice_prerequisites": (await service.readiness(db, milestone)).model_dump(mode="json"),
                    }
            else:
                payload = origin(ctx)
                kind = {
                    "collect_milestone_evidence": "collect",
                    "verify_milestone": "verify",
                    "prepare_acceptance_package": "package",
                }[name]
                if isinstance(args, CollectArgs):
                    if args.source_id:
                        kind = "source_evidence"
                        source_hash = await source_authority(db, ctx, args.source_id)
                        payload.update(
                            {
                                "source_id": str(args.source_id),
                                "source_hash": source_hash,
                                "requirement_ids": [str(value) for value in args.requirement_ids],
                                "kind": args.kind,
                                "customer_safe": False,
                            }
                        )
                    else:
                        if not project.configuration.get("github_account_id") or not milestone.specification.get(
                            "expected_sha"
                        ):
                            raise HTTPException(409, "Approve an exact selected repository commit before collection")
                        payload.update(
                            {
                                "run_id": args.run_id,
                                "workflow_id": args.workflow_id,
                                "artifact_names": args.artifact_names,
                            }
                        )
                job = await service.milestone_job(db, milestone.id, kind, payload)
                data = {"job": service.job_view(job), "status": "queued_local_work", "delivered": False}
        else:
            raise ValueError("Unsupported ProofOS arguments")
        entry = material(
            json.dumps(data, default=str, ensure_ascii=False), "ProofOS " + name.replace("_", " "), reference
        )
        return {"materials": [entry.model_dump(mode="json")], "classification": PROOF_TOOL_KINDS[name]}
