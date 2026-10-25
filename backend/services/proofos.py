"""Transactional owner domain operations; immutable approval and financial boundaries."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.core import records, strings
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
from backend.repositories.parsed import parsed_source
from backend.schemas.pipeline import digest
from backend.schemas.proofos import (
    ApprovalCreate,
    ApprovalDecision,
    ContractCreate,
    MilestoneCreate,
    PackageReview,
    ProjectCreate,
    RequirementAssign,
    RequirementReview,
)
from backend.schemas.verification import Criterion, EvidenceFact, InvoiceReadiness, VerificationResult
from backend.security.policy import validate_disclosure
from backend.security.runtime import configured_secrets
from backend.services.access import owner_id
from backend.services.proofos_access import (
    account_access,
    contract_current,
    lock_verification_authority,
    owned,
    project_access,
    verification_access,
)
from backend.services.verification import POLICY_VERSION as VERIFICATION_POLICY_VERSION
from backend.services.verification import invoice_readiness, verify


def audit(db: AsyncSession, project: ProofProject, event: str, **details: object) -> None:
    db.add(
        ProofAudit(
            owner_id=project.owner_id, project_id=project.id, actor_id=owner_id(db), event=event, details=details
        )
    )


def basic(row: object, fields: list[str]) -> dict[str, object]:
    return {name: getattr(row, name) for name in fields}


def project_view(row: ProofProject) -> dict[str, object]:
    return {**row.configuration, **basic(row, ["id", "name", "customer_name", "created_at"])}


def contract_view(row: ProofContract) -> dict[str, object]:
    return basic(
        row,
        ["id", "project_id", "source_id", "title", "source_hash", "source_version", "revision", "state", "created_at"],
    )


def requirement_view(row: ProofRequirement) -> dict[str, object]:
    return {
        **row.proposal,
        **basic(row, ["id", "contract_id", "milestone_id", "stable_id", "original_text", "state", "created_at"]),
    }


def verification_view(row: ProofVerification) -> dict[str, object]:
    return basic(row, ["id", "milestone_id", "input_hash", "result", "created_at"])


def job_view(row: ProofJob) -> dict[str, object]:
    return basic(row, ["id", "project_id", "kind", "status", "error", "result", "created_at"])


def invoice_view(row: ProofInvoice) -> dict[str, object]:
    return {**row.payload, **basic(row, ["id", "milestone_id", "package_id", "created_at"]), "status": "draft"}


async def package_view(db: AsyncSession, row: ProofPackage) -> dict[str, object]:
    approvals = list(await db.scalars(select(ProofApproval).where(ProofApproval.package_id == row.id)))
    internal = next((value.status for value in approvals if value.kind == "internal"), "pending")
    customer = next((value.status for value in approvals if value.kind == "customer"), "pending")
    return {
        **basic(
            row, ["id", "milestone_id", "verification_id", "version", "customer_hash", "internal_hash", "created_at"]
        ),
        "internal_review": internal,
        "customer_status": customer,
    }


async def approval_view(db: AsyncSession, row: ProofApproval) -> dict[str, object]:
    project = await db.get(ProofProject, row.project_id)
    return {
        **basic(row, ["id", "project_id", "package_id", "package_hash", "expires_at", "status", "decided_at"]),
        "customer_subject": row.subject,
        "title": project.name if project else "Review",
        "customer_name": project.customer_name if project else "",
    }


async def latest_verification(db: AsyncSession, milestone: ProofMilestone) -> ProofVerification | None:
    result = await db.scalars(
        select(ProofVerification)
        .where(
            ProofVerification.milestone_id == milestone.id,
            ProofVerification.owner_id == milestone.owner_id,
        )
        .order_by(ProofVerification.created_at.desc(), ProofVerification.id.desc())
        .limit(1)
    )
    return result.first()


async def latest_package(db: AsyncSession, milestone: ProofMilestone) -> ProofPackage | None:
    result = await db.scalars(
        select(ProofPackage)
        .where(ProofPackage.milestone_id == milestone.id)
        .order_by(ProofPackage.version.desc())
        .limit(1)
    )
    return result.first()


async def readiness(db: AsyncSession, milestone: ProofMilestone) -> InvoiceReadiness:
    if not milestone.scope_approved or digest(milestone.specification) != milestone.scope_hash:
        return InvoiceReadiness(eligible=False, reasons=["Approved contractual scope is unavailable or changed"])
    verification = await latest_verification(db, milestone)
    if verification is None:
        return InvoiceReadiness(eligible=False, reasons=["Run deterministic verification first"])
    if verification.result.get("policy_version") != VERIFICATION_POLICY_VERSION:
        return InvoiceReadiness(
            eligible=False, reasons=["Verification policy changed; run a new verification and package"]
        )
    try:
        await contract_current(db, milestone.contract_id)
        await verification_access(db, verification)
    except HTTPException:
        return InvoiceReadiness(eligible=False, reasons=["Current contract or evidence access/version requires review"])
    package = await latest_package(db, milestone)
    approvals = (
        list(await db.scalars(select(ProofApproval).where(ProofApproval.package_id == package.id))) if package else []
    )
    spec = milestone.specification
    result = invoice_readiness(
        current_result(verification),
        internal_approved=bool(
            package
            and package.verification_id == verification.id
            and any(
                row.kind == "internal" and row.status == "approved" and row.package_hash == package.internal_hash
                for row in approvals
            )
        ),
        customer_approved=bool(
            package
            and package.verification_id == verification.id
            and any(
                row.kind == "customer" and row.status == "accepted" and row.package_hash == package.customer_hash
                for row in approvals
            )
        ),
        contractual_terms_satisfied=bool(
            milestone.scope_approved and spec.get("amount_minor") and spec.get("currency") and spec.get("payment_terms")
        ),
    )
    if verification.snapshot.get("evidence_hash") != await evidence_fingerprint(db, milestone.id):
        return InvoiceReadiness(
            eligible=False, reasons=[*result.reasons, "New evidence requires a new verification and package"]
        )
    return result


async def evidence_fingerprint(db: AsyncSession, milestone_id: UUID) -> str:
    values = await db.scalars(
        select(ProofEvidence.identity_hash)
        .where(ProofEvidence.milestone_id == milestone_id)
        .order_by(ProofEvidence.created_at, ProofEvidence.id)
    )
    return digest(list(values))


def current_result(verification: ProofVerification) -> VerificationResult:
    if verification.result.get("policy_version") != VERIFICATION_POLICY_VERSION:
        raise HTTPException(409, "Verification policy changed; run a new verification and package")
    criteria = [Criterion.model_validate(value) for value in records(verification.snapshot.get("criteria", []))]
    evidence = [EvidenceFact.model_validate(value) for value in records(verification.snapshot.get("facts", []))]
    original = VerificationResult.model_validate(verification.result)
    reproduced = verify(criteria, evidence, approved=True, now=original.verified_at)
    if reproduced.input_hash != verification.input_hash or digest(reproduced.model_dump(mode="json")) != digest(
        verification.result
    ):
        raise HTTPException(409, "Stored verification integrity failed; run a new verification")
    return verify(
        criteria,
        evidence,
        approved=True,
        now=datetime.now(UTC),
    )


async def milestone_view(db: AsyncSession, row: ProofMilestone) -> dict[str, object]:
    verification = await latest_verification(db, row)
    return {
        **row.specification,
        **basic(row, ["id", "project_id", "contract_id", "code", "title", "scope_approved", "created_at"]),
        "readiness": (await readiness(db, row)).model_dump(mode="json"),
        "latest_verification": verification_view(verification) if verification else None,
    }


async def create_project(db: AsyncSession, payload: ProjectCreate) -> ProofProject:
    validate_disclosure(payload.model_dump(mode="json"), configured_secrets())
    if payload.customer_approver_id == owner_id(db):
        raise HTTPException(422, "Customer acceptance authority must be a different authenticated account")
    if payload.github_account_id:
        account = await account_access(db, owner_id(db), payload.github_account_id)
        selection = account.configuration
        if account.provider != "github" or payload.repository_id not in strings(selection.get("resource_ids", [])):
            raise HTTPException(422, "Choose a repository selected on your GitHub connection")
        branches = selection.get("branches", {})
        if isinstance(branches, dict) and branches.get(payload.repository_id, "main") != payload.branch:
            raise HTTPException(422, "Project branch must match the selected repository branch")
    row = ProofProject(
        owner_id=owner_id(db),
        name=payload.name,
        customer_name=payload.customer_name,
        configuration=payload.model_dump(mode="json"),
    )
    db.add(row)
    await db.flush()
    audit(db, row, "project_created")
    await db.commit()
    return row


async def enqueue(db: AsyncSession, project: ProofProject, kind: str, payload: dict[str, object]) -> ProofJob:
    owner_key = int.from_bytes(bytes.fromhex(digest(["proofos-capacity", owner_id(db)]))[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(owner_key)))
    await owned(db, ProofProject, project.id, lock=True)
    await project_access(db, project)
    payload = {**payload, "project_hash": digest(project.configuration)}
    count = await db.scalar(
        select(func.count())
        .select_from(ProofJob)
        .where(ProofJob.owner_id == owner_id(db), ProofJob.status.in_(["queued", "running"]))
    )
    if count is not None and count >= 5:
        raise HTTPException(429, "ProofOS job capacity reached", headers={"Retry-After": "10"})
    intent = digest([kind, payload])
    previous = await db.scalar(
        select(ProofJob).where(ProofJob.project_id == project.id, ProofJob.intent_hash == intent)
    )
    if previous:
        if previous.status in {"failed", "cancelled"}:
            previous.status, previous.error, previous.attempts = "queued", None, 0
            previous.available_at = datetime.now(UTC)
            await db.commit()
        return previous
    row = ProofJob(owner_id=project.owner_id, project_id=project.id, kind=kind, intent_hash=intent, payload=payload)
    db.add(row)
    await db.flush()
    audit(db, project, "job_queued", job_id=str(row.id), kind=kind)
    await db.commit()
    return row


async def milestone_job(db: AsyncSession, identifier: UUID, kind: str, payload: dict[str, object]) -> ProofJob:
    milestone = await owned(db, ProofMilestone, identifier)
    project = await owned(db, ProofProject, milestone.project_id)
    if not milestone.scope_approved or digest(milestone.specification) != milestone.scope_hash:
        raise HTTPException(409, "Milestone scope requires approval")
    evidence = list(
        await db.scalars(
            select(ProofEvidence)
            .where(ProofEvidence.milestone_id == identifier)
            .order_by(ProofEvidence.created_at, ProofEvidence.id)
        )
    )
    data = {
        **payload,
        "milestone_id": str(identifier),
        "scope_hash": milestone.scope_hash,
        "evidence_hash": digest([row.identity_hash for row in evidence]),
    }
    latest = await latest_verification(db, milestone)
    if kind == "verify":
        data["previous_verification_id"] = str(latest.id) if latest else None
    if kind == "package":
        if latest is None:
            raise HTTPException(409, "Run verification before generating a package")
        data["verification_id"] = str(latest.id)
    return await enqueue(db, project, kind, data)


async def extract(
    db: AsyncSession, project_id: UUID, payload: ContractCreate, *, origin: dict[str, object] | None = None
) -> ProofJob:
    project = await owned(db, ProofProject, project_id)
    parsed = await parsed_source(db, payload.source_id, owner_id(db))
    if parsed is None:
        raise HTTPException(404, "Accessible parsed contract not found")
    return await enqueue(
        db,
        project,
        "extract",
        {
            **(origin or {}),
            **payload.model_dump(mode="json"),
            "source_hash": parsed.canonical.source.sha256,
            "canonical_hash": parsed.canonical.content_hash,
        },
    )


async def review_requirement(db: AsyncSession, identifier: UUID, payload: RequirementReview) -> ProofRequirement:
    requirement = await owned(db, ProofRequirement, identifier)
    project = await owned(db, ProofProject, requirement.project_id, lock=True)
    await project_access(db, project)
    contract = await owned(db, ProofContract, requirement.contract_id)
    if contract.state != "DRAFT":
        raise HTTPException(409, "Approved contractual wording is immutable; create an amendment")
    validate_disclosure(payload.model_dump(mode="json"), configured_secrets())
    all_requirements = list(
        await db.scalars(
            select(ProofRequirement).where(
                ProofRequirement.contract_id == contract.id, ProofRequirement.id != requirement.id
            )
        )
    )
    existing_ids = {
        str(item["id"]) for other in all_requirements for item in records(other.proposal.get("acceptance_criteria", []))
    }
    if any(criterion.id in existing_ids for criterion in payload.acceptance_criteria):
        raise HTTPException(422, "Criterion IDs must be unique within a contract")
    requirement.proposal = {**requirement.proposal, **payload.model_dump(mode="json", exclude={"approve"})}
    requirement.state = "APPROVED" if payload.approve else "NEEDS_REVIEW"
    audit(db, project, "requirement_reviewed", requirement_id=str(identifier), state=requirement.state)
    await db.commit()
    return requirement


async def approve_contract(db: AsyncSession, identifier: UUID) -> ProofContract:
    contract = await owned(db, ProofContract, identifier)
    project = await owned(db, ProofProject, contract.project_id, lock=True)
    await project_access(db, project)
    if contract.state != "DRAFT":
        raise HTTPException(409, "Contract version was already finalized")
    parsed = await parsed_source(db, contract.source_id, owner_id(db)) if contract.source_id else None
    if parsed is None or parsed.canonical.source.sha256 != contract.source_hash:
        raise HTTPException(409, "Contract source changed; extract a new revision")
    requirements = list(await db.scalars(select(ProofRequirement).where(ProofRequirement.contract_id == identifier)))
    if not requirements or any(row.state != "APPROVED" for row in requirements):
        raise HTTPException(409, "Review every proposed requirement before approving the contract")
    contract.state = "APPROVED"
    contract.snapshot = {**contract.snapshot, "approved_by": owner_id(db), "approved_at": datetime.now(UTC).isoformat()}
    audit(db, project, "contract_approved", contract_id=str(identifier), revision=contract.revision)
    await db.commit()
    return contract


async def create_milestone(db: AsyncSession, project_id: UUID, payload: MilestoneCreate) -> ProofMilestone:
    project = await owned(db, ProofProject, project_id, lock=True)
    await project_access(db, project)
    contract = await owned(db, ProofContract, payload.contract_id)
    if contract.project_id != project.id:
        raise HTTPException(404, "Contract not found in this project")
    if await db.scalar(
        select(ProofMilestone.id).where(ProofMilestone.project_id == project.id, ProofMilestone.code == payload.code)
    ):
        raise HTTPException(409, "Milestone code already exists")
    row = ProofMilestone(
        owner_id=project.owner_id,
        project_id=project.id,
        contract_id=contract.id,
        code=payload.code,
        title=payload.title,
        specification=payload.model_dump(mode="json"),
    )
    db.add(row)
    await db.flush()
    audit(db, project, "milestone_created", milestone_id=str(row.id))
    await db.commit()
    return row


async def assign_requirement(db: AsyncSession, identifier: UUID, payload: RequirementAssign) -> ProofRequirement:
    row = await owned(db, ProofRequirement, identifier)
    project = await owned(db, ProofProject, row.project_id, lock=True)
    milestone = await owned(db, ProofMilestone, payload.milestone_id)
    if milestone.project_id != row.project_id or milestone.contract_id != row.contract_id:
        raise HTTPException(404, "Milestone and requirement must use the same project and contract version")
    previous = await db.get(ProofMilestone, row.milestone_id) if row.milestone_id else None
    if milestone.scope_approved or (previous and previous.scope_approved):
        raise HTTPException(409, "Approved milestone scope is immutable; create a new milestone")
    row.milestone_id = milestone.id
    audit(db, project, "requirement_assigned", requirement_id=str(identifier), milestone_id=str(milestone.id))
    await db.commit()
    return row


async def approve_milestone(db: AsyncSession, identifier: UUID) -> ProofMilestone:
    row = await owned(db, ProofMilestone, identifier)
    project = await owned(db, ProofProject, row.project_id, lock=True)
    await project_access(db, project, current=True)
    await contract_current(db, row.contract_id)
    contract = await owned(db, ProofContract, row.contract_id)
    requirements = list(await db.scalars(select(ProofRequirement).where(ProofRequirement.milestone_id == row.id)))
    if (
        row.scope_approved
        or contract.state != "APPROVED"
        or not requirements
        or any(item.state != "APPROVED" for item in requirements)
    ):
        raise HTTPException(409, "Approve the contract and assign reviewed requirements before freezing scope")
    criteria: list[Criterion] = []
    for requirement in requirements:
        for value in records(requirement.proposal["acceptance_criteria"]):
            criterion = Criterion.model_validate(value)
            if criterion.kind in {"ci", "deployment", "review"}:
                pinned = {
                    "repository_id": project.configuration.get("repository_id"),
                    "branch": project.configuration.get("branch"),
                    "sha": row.specification.get("expected_sha"),
                }
                if not all(pinned.values()):
                    raise HTTPException(
                        422, "Native checks require an exact selected repository, branch and commit SHA"
                    )
                if any(getattr(criterion, key) not in {None, value} for key, value in pinned.items()):
                    raise HTTPException(422, "Criterion identity conflicts with the approved milestone commit")
                criterion = Criterion.model_validate({**criterion.model_dump(), **pinned})
            criteria.append(criterion)
    row.specification = {
        **row.specification,
        "criteria": [value.model_dump(mode="json") for value in criteria],
        "requirements": [str(value.id) for value in requirements],
    }
    row.scope_hash, row.scope_approved = digest(row.specification), True
    audit(db, project, "scope_approved", milestone_id=str(row.id), scope_hash=row.scope_hash)
    await db.commit()
    return row


async def review_package(db: AsyncSession, identifier: UUID, payload: PackageReview) -> ProofPackage:
    package = await owned(db, ProofPackage, identifier)
    verification = await owned(db, ProofVerification, package.verification_id)
    await lock_verification_authority(db, verification)
    project = await owned(db, ProofProject, package.project_id, lock=True)
    await project_access(db, project)
    verification = await owned(db, ProofVerification, package.verification_id)
    await verification_access(db, verification)
    if package.internal_hash != payload.expected_hash:
        raise HTTPException(409, "Package review hash does not match")
    if await db.scalar(
        select(ProofApproval.id).where(ProofApproval.package_id == package.id, ProofApproval.kind == "internal")
    ):
        raise HTTPException(409, "This package already has an immutable internal review")
    db.add(
        ProofApproval(
            owner_id=project.owner_id,
            project_id=project.id,
            package_id=package.id,
            kind="internal",
            subject=owner_id(db),
            package_hash=package.internal_hash,
            status=payload.decision,
            reason=payload.reason,
            decided_at=datetime.now(UTC),
        )
    )
    audit(db, project, "package_reviewed", package_id=str(package.id), decision=payload.decision)
    await db.commit()
    return package


async def request_approval(db: AsyncSession, identifier: UUID, payload: ApprovalCreate) -> ProofApproval:
    package = await owned(db, ProofPackage, identifier)
    bound_verification = await owned(db, ProofVerification, package.verification_id)
    await lock_verification_authority(db, bound_verification)
    project = await owned(db, ProofProject, package.project_id, lock=True)
    await project_access(db, project, current=True)
    milestone = await owned(db, ProofMilestone, package.milestone_id)
    await contract_current(db, milestone.contract_id)
    newest = await latest_package(db, milestone)
    verification = await latest_verification(db, milestone)
    if verification:
        await verification_access(db, verification)
    subject = project.configuration.get("customer_approver_id")
    internal = await db.scalar(
        select(ProofApproval).where(
            ProofApproval.package_id == package.id, ProofApproval.kind == "internal", ProofApproval.status == "approved"
        )
    )
    result = current_result(verification) if verification else None
    if verification and verification.snapshot.get("evidence_hash") != await evidence_fingerprint(db, milestone.id):
        raise HTTPException(409, "New evidence requires a new verification and package")
    if (
        not subject
        or subject == owner_id(db)
        or not internal
        or not newest
        or newest.id != package.id
        or not verification
        or package.verification_id != verification.id
    ):
        raise HTTPException(409, "Current package needs internal approval and a separate configured customer authority")
    if (
        result is None
        or result.technical_status not in {"PASSED", "NOT_APPLICABLE"}
        or result.delivery_status not in {"PASSED", "NOT_APPLICABLE"}
    ):
        raise HTTPException(409, "Mandatory technical and delivery verification must pass before customer review")
    existing = await db.scalar(
        select(ProofApproval).where(ProofApproval.package_id == package.id, ProofApproval.kind == "customer")
    )
    if existing:
        return existing
    row = ProofApproval(
        owner_id=project.owner_id,
        project_id=project.id,
        package_id=package.id,
        kind="customer",
        subject=str(subject),
        package_hash=package.customer_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=payload.expires_in_hours),
    )
    db.add(row)
    await db.flush()
    audit(db, project, "customer_review_requested", approval_id=str(row.id), package_id=str(package.id))
    await db.commit()
    return row


async def customer_review(db: AsyncSession, identifier: UUID) -> tuple[ProofApproval, ProofPackage]:
    approval = await db.scalar(
        select(ProofApproval).where(
            ProofApproval.id == identifier, ProofApproval.kind == "customer", ProofApproval.subject == owner_id(db)
        )
    )
    if approval is None:
        raise HTTPException(404, "Customer review not found")
    project = await db.get(ProofProject, approval.project_id)
    package = await db.get(ProofPackage, approval.package_id)
    if project is None or package is None or project.configuration.get("customer_approver_id") != owner_id(db):
        raise HTTPException(404, "Customer review authority changed")
    await project_access(db, project)
    if approval.status == "pending" and (not approval.expires_at or approval.expires_at <= datetime.now(UTC)):
        raise HTTPException(409, "Customer review request expired")
    if approval.package_hash != package.customer_hash:
        raise HTTPException(409, "Customer package identity changed")
    return approval, package


async def decide(db: AsyncSession, identifier: UUID, payload: ApprovalDecision) -> ProofApproval:
    approval, package = await customer_review(db, identifier)
    bound_verification = await db.get(ProofVerification, package.verification_id, populate_existing=True)
    if bound_verification is None:
        raise HTTPException(409, "Package verification is unavailable")
    await lock_verification_authority(db, bound_verification)
    project = await db.get(ProofProject, approval.project_id, with_for_update=True, populate_existing=True)
    await db.refresh(approval, with_for_update=True)
    milestone = await db.get(ProofMilestone, package.milestone_id)
    if milestone:
        await contract_current(db, milestone.contract_id)
    newest = await latest_package(db, milestone) if milestone else None
    verification = await latest_verification(db, milestone) if milestone else None
    if verification:
        await verification_access(db, verification)
    if (
        verification
        and milestone
        and verification.snapshot.get("evidence_hash") != await evidence_fingerprint(db, milestone.id)
    ):
        raise HTTPException(409, "New evidence requires a new verification and review")
    if project is None or approval.status != "pending" or approval.package_hash != payload.expected_hash:
        raise HTTPException(409, "Review was already decided or the exact package hash differs")
    await project_access(db, project)
    if project.configuration.get("customer_approver_id") != owner_id(db) or approval.subject != owner_id(db):
        raise HTTPException(403, "Customer acceptance authority changed")
    fresh_result = current_result(verification) if verification else None
    if fresh_result is None or any(
        status not in {"PASSED", "NOT_APPLICABLE"}
        for status in (fresh_result.technical_status, fresh_result.delivery_status)
    ):
        raise HTTPException(409, "Current technical and delivery verification must pass before acceptance")
    if (
        not approval.expires_at
        or approval.expires_at <= datetime.now(UTC)
        or not newest
        or newest.id != package.id
        or not verification
        or package.verification_id != verification.id
    ):
        raise HTTPException(409, "Review request expired or a newer verification/package requires review")
    approval.status, approval.reason, approval.decided_at = payload.decision, payload.reason, datetime.now(UTC)
    audit(
        db,
        project,
        "customer_decided",
        approval_id=str(approval.id),
        decision=payload.decision,
        package_hash=approval.package_hash,
    )
    await db.commit()
    return approval


async def prepare_invoice(db: AsyncSession, identifier: UUID) -> ProofInvoice:
    milestone = await owned(db, ProofMilestone, identifier)
    verification = await latest_verification(db, milestone)
    await db.commit()
    if verification:
        await verification_access(db, verification, native=True)
        await lock_verification_authority(db, verification)
    project = await owned(db, ProofProject, milestone.project_id, lock=True)
    await project_access(db, project, current=True)
    await contract_current(db, milestone.contract_id)
    if verification:
        await verification_access(db, verification)
    ready = await readiness(db, milestone)
    if not ready.eligible:
        raise HTTPException(409, "Invoice draft blocked: " + "; ".join(ready.reasons))
    previous = await db.scalar(select(ProofInvoice).where(ProofInvoice.milestone_id == milestone.id))
    if previous:
        return previous
    package = await latest_package(db, milestone)
    if package is None:
        raise HTTPException(409, "An approved acceptance package is required")
    payload = {name: milestone.specification[name] for name in ["amount_minor", "currency", "payment_terms"]}
    payload.update(
        {
            "customer_name": project.customer_name,
            "contract_id": str(milestone.contract_id),
            "taxes": None,
            "bank_details": None,
            "issued": False,
            "sent": False,
        }
    )
    row = ProofInvoice(
        owner_id=project.owner_id,
        project_id=project.id,
        milestone_id=milestone.id,
        package_id=package.id,
        payload=payload,
    )
    db.add(row)
    await db.flush()
    audit(db, project, "invoice_draft_prepared", invoice_id=str(row.id), package_id=str(package.id))
    await db.commit()
    return row
