"""Private project delivery APIs and authenticated, version-bound customer reviews."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.proofos import (
    ProofApproval,
    ProofAudit,
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
from backend.db.session import get_session
from backend.repositories.parsed import parsed_source
from backend.schemas.pipeline import digest
from backend.schemas.proofos import (
    ApprovalCreate,
    ApprovalDecision,
    CollectEvidence,
    ContractCreate,
    MilestoneCreate,
    PackageReview,
    ProjectCreate,
    RequirementAssign,
    RequirementReview,
    SourceEvidence,
)
from backend.services import proofos as service
from backend.services.acceptance_package import render_package
from backend.services.access import owner_id
from backend.services.proofos_access import evidence_access, owned, project_access, verification_access
from backend.services.proofos_jobs import criterion_list, current_evidence

router = APIRouter(prefix="/proofos")
Database = Annotated[AsyncSession, Depends(get_session)]


@router.get("/projects")
async def projects(db: Database) -> list[dict[str, object]]:
    rows = await db.scalars(
        select(ProofProject)
        .where(ProofProject.owner_id == owner_id(db))
        .order_by(ProofProject.created_at.desc())
        .limit(100)
    )
    return [service.project_view(row) for row in rows]


@router.post("/projects", status_code=201)
async def create_project(payload: ProjectCreate, db: Database) -> dict[str, object]:
    return service.project_view(await service.create_project(db, payload))


@router.get("/projects/{identifier}")
async def detail(identifier: UUID, db: Database) -> dict[str, object]:
    project = await owned(db, ProofProject, identifier)
    await project_access(db, project)
    contracts = list(
        await db.scalars(
            select(ProofContract)
            .where(ProofContract.project_id == identifier)
            .order_by(ProofContract.revision.desc())
            .limit(100)
        )
    )
    contracts.reverse()
    requirements = list(
        await db.scalars(
            select(ProofRequirement)
            .where(ProofRequirement.project_id == identifier)
            .order_by(ProofRequirement.created_at.desc(), ProofRequirement.stable_id)
            .limit(500)
        )
    )
    milestones = list(
        await db.scalars(
            select(ProofMilestone)
            .where(ProofMilestone.project_id == identifier)
            .order_by(ProofMilestone.created_at)
            .limit(100)
        )
    )
    evidence = list(
        await db.scalars(
            select(ProofEvidence)
            .where(ProofEvidence.project_id == identifier)
            .order_by(ProofEvidence.created_at.desc())
            .limit(500)
        )
    )
    for row in evidence:
        await evidence_access(db, row, current=False)
    current_ids = {row.id for row in current_evidence(evidence)}
    verifications = list(
        await db.scalars(
            select(ProofVerification)
            .where(ProofVerification.project_id == identifier)
            .order_by(ProofVerification.created_at.desc())
            .limit(100)
        )
    )
    packages = list(
        await db.scalars(
            select(ProofPackage)
            .where(ProofPackage.project_id == identifier)
            .order_by(ProofPackage.created_at.desc())
            .limit(100)
        )
    )
    approvals = list(
        await db.scalars(
            select(ProofApproval)
            .where(ProofApproval.project_id == identifier, ProofApproval.kind == "customer")
            .order_by(ProofApproval.created_at.desc())
            .limit(100)
        )
    )
    invoices = list(await db.scalars(select(ProofInvoice).where(ProofInvoice.project_id == identifier).limit(100)))
    jobs = list(
        await db.scalars(
            select(ProofJob).where(ProofJob.project_id == identifier).order_by(ProofJob.created_at.desc()).limit(100)
        )
    )
    audit = list(
        await db.scalars(
            select(ProofAudit)
            .where(ProofAudit.project_id == identifier)
            .order_by(ProofAudit.created_at.desc())
            .limit(100)
        )
    )
    return {
        "project": service.project_view(project),
        "contracts": [service.contract_view(row) for row in contracts],
        "requirements": [service.requirement_view(row) for row in requirements],
        "milestones": [await service.milestone_view(db, row) for row in milestones],
        "evidence": [
            {**service.basic(row, ["id", "milestone_id", "fact", "created_at"]), "current": row.id in current_ids}
            for row in evidence
        ],
        "verifications": [service.verification_view(row) for row in verifications],
        "packages": [await service.package_view(db, row) for row in packages],
        "approvals": [await service.approval_view(db, row) for row in approvals],
        "invoices": [service.invoice_view(row) for row in invoices],
        "jobs": [service.job_view(row) for row in jobs],
        "audit": [service.basic(row, ["id", "actor_id", "event", "details", "created_at"]) for row in audit],
        "coverage": (
            "Latest bounded pages: at most 100 contracts/milestones/history entries and 500 requirements/evidence"
        ),
    }


@router.post("/projects/{identifier}/contracts", status_code=202)
async def extract(identifier: UUID, payload: ContractCreate, db: Database) -> dict[str, object]:
    return service.job_view(await service.extract(db, identifier, payload))


@router.post("/contracts/{identifier}/approve")
async def approve_contract(identifier: UUID, db: Database) -> dict[str, object]:
    return service.contract_view(await service.approve_contract(db, identifier))


@router.post("/requirements/{identifier}/review")
async def review_requirement(identifier: UUID, payload: RequirementReview, db: Database) -> dict[str, object]:
    return service.requirement_view(await service.review_requirement(db, identifier, payload))


@router.post("/requirements/{identifier}/assign")
async def assign(identifier: UUID, payload: RequirementAssign, db: Database) -> dict[str, object]:
    return service.requirement_view(await service.assign_requirement(db, identifier, payload))


@router.post("/projects/{identifier}/milestones", status_code=201)
async def create_milestone(identifier: UUID, payload: MilestoneCreate, db: Database) -> dict[str, object]:
    return await service.milestone_view(db, await service.create_milestone(db, identifier, payload))


@router.post("/milestones/{identifier}/approve")
async def approve_milestone(identifier: UUID, db: Database) -> dict[str, object]:
    return await service.milestone_view(db, await service.approve_milestone(db, identifier))


async def milestone_job(db: AsyncSession, identifier: UUID, kind: str, payload: dict[str, object]) -> ProofJob:
    return await service.milestone_job(db, identifier, kind, payload)


@router.post("/milestones/{identifier}/collect", status_code=202)
async def collect(identifier: UUID, payload: CollectEvidence, db: Database) -> dict[str, object]:
    milestone = await owned(db, ProofMilestone, identifier)
    project = await owned(db, ProofProject, milestone.project_id)
    if not project.configuration.get("github_account_id") or not milestone.specification.get("expected_sha"):
        raise HTTPException(409, "Connect a selected GitHub repository and approve an exact commit first")
    return service.job_view(await milestone_job(db, identifier, "collect", payload.model_dump(mode="json")))


@router.post("/milestones/{identifier}/evidence", status_code=202)
async def add_evidence(identifier: UUID, payload: SourceEvidence, db: Database) -> dict[str, object]:
    parsed = await parsed_source(db, payload.source_id, owner_id(db))
    if parsed is None:
        raise HTTPException(404, "Accessible parsed evidence source not found")
    return service.job_view(
        await milestone_job(
            db,
            identifier,
            "source_evidence",
            {
                **payload.model_dump(mode="json"),
                "source_hash": parsed.canonical.source.sha256,
            },
        )
    )


@router.post("/milestones/{identifier}/verify", status_code=202)
async def verify(identifier: UUID, db: Database) -> dict[str, object]:
    return service.job_view(await milestone_job(db, identifier, "verify", {}))


@router.post("/milestones/{identifier}/packages", status_code=202)
async def package(identifier: UUID, db: Database) -> dict[str, object]:
    return service.job_view(await milestone_job(db, identifier, "package", {}))


@router.get("/jobs/{identifier}")
async def job_status(identifier: UUID, db: Database) -> dict[str, object]:
    return service.job_view(await owned(db, ProofJob, identifier))


@router.post("/jobs/{identifier}/cancel")
async def cancel_job(identifier: UUID, db: Database) -> dict[str, object]:
    job = await owned(db, ProofJob, identifier, lock=True)
    if job.status in {"queued", "running"}:
        job.status, job.run_token, job.lease_until = "cancelled", None, None
        job.error = "Cancelled by the owner"
    await db.commit()
    return service.job_view(job)


async def package_authority(db: AsyncSession, package: ProofPackage, *, native: bool = False) -> None:
    project = await db.get(ProofProject, package.project_id, populate_existing=True)
    if project is None:
        raise HTTPException(404, "Package not found")
    await project_access(db, project)
    verification = await db.get(ProofVerification, package.verification_id, populate_existing=True)
    if verification is None:
        raise HTTPException(409, "Package verification is unavailable")
    await verification_access(db, verification, native=native, current=False)


@router.get("/packages/{identifier}")
async def package_detail(identifier: UUID, db: Database) -> dict[str, object]:
    package = await owned(db, ProofPackage, identifier)
    await package_authority(db, package)
    return {
        "package": await service.package_view(db, package),
        "internal": package.internal,
        "customer": package.customer,
    }


async def download(db: AsyncSession, package: ProofPackage, audience: str, format: str) -> Response:
    await db.commit()
    await package_authority(db, package, native=True)
    payload = package.customer if audience == "customer" else package.internal
    if digest(payload) != (package.customer_hash if audience == "customer" else package.internal_hash):
        raise HTTPException(409, "Package hash validation failed")
    content = (
        await asyncio.to_thread(render_package, payload)
        if format == "pdf"
        else json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode()
    )
    await package_authority(db, package)
    return Response(
        content,
        media_type="application/pdf" if format == "pdf" else "application/json",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="ProofOS-package-v{package.version}-{audience}.{format}"',
        },
    )


@router.get("/packages/{identifier}/download/{audience}/{format}")
async def package_download(
    identifier: UUID, audience: Literal["internal", "customer"], format: Literal["pdf", "json"], db: Database
) -> Response:
    return await download(db, await owned(db, ProofPackage, identifier), audience, format)


@router.post("/packages/{identifier}/review")
async def review_package(identifier: UUID, payload: PackageReview, db: Database) -> dict[str, object]:
    return await service.package_view(db, await service.review_package(db, identifier, payload))


@router.post("/packages/{identifier}/request-approval")
async def request_approval(identifier: UUID, payload: ApprovalCreate, db: Database) -> dict[str, object]:
    package = await owned(db, ProofPackage, identifier)
    await db.commit()
    await package_authority(db, package, native=True)
    return await service.approval_view(db, await service.request_approval(db, identifier, payload))


@router.get("/approvals")
async def approvals(db: Database) -> list[dict[str, object]]:
    rows = await db.scalars(
        select(ProofApproval)
        .where(ProofApproval.subject == owner_id(db), ProofApproval.kind == "customer")
        .order_by(ProofApproval.created_at.desc())
        .limit(100)
    )
    return [await service.approval_view(db, row) for row in rows]


@router.get("/approvals/{identifier}")
async def customer_package(identifier: UUID, db: Database) -> dict[str, object]:
    approval, package = await service.customer_review(db, identifier)
    await package_authority(db, package)
    return {"approval": await service.approval_view(db, approval), "package": package.customer}


@router.get("/approvals/{identifier}/download/{format}")
async def customer_download(identifier: UUID, format: Literal["pdf", "json"], db: Database) -> Response:
    _, package = await service.customer_review(db, identifier)
    return await download(db, package, "customer", format)


@router.post("/approvals/{identifier}/decide")
async def decision(identifier: UUID, payload: ApprovalDecision, db: Database) -> dict[str, object]:
    _, package = await service.customer_review(db, identifier)
    await db.commit()
    await package_authority(db, package, native=True)
    return await service.approval_view(db, await service.decide(db, identifier, payload))


@router.post("/milestones/{identifier}/invoice-draft")
async def invoice(identifier: UUID, db: Database) -> dict[str, object]:
    return service.invoice_view(await service.prepare_invoice(db, identifier))


@router.get("/milestones/{identifier}/task-packet")
async def task_packet(identifier: UUID, db: Database) -> dict[str, object]:
    milestone = await owned(db, ProofMilestone, identifier)
    project = await owned(db, ProofProject, milestone.project_id)
    await project_access(db, project)
    criteria = criterion_list(milestone)
    return {
        "format": "proofos-engineering-task-v1",
        "milestone_id": str(milestone.id),
        "repository": project.configuration.get("repository_name"),
        "base_sha": milestone.specification.get("expected_sha"),
        "approved_requirement_ids": milestone.specification.get("requirements"),
        "acceptance_criteria": [criterion.model_dump(mode="json") for criterion in criteria],
        "branch_prefix": "codex/",
        "execution_authorized": False,
        "runner": "not_configured",
        "constraints": [
            "Separate explicit repository and runner authorization required",
            "Isolated workspace and feature branch",
            "Bounded tools and file scope",
            "Human review before merge",
            "Actual GitHub CI independently verified by ProofOS",
            "Coding output cannot grant customer acceptance",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
