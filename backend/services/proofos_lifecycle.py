"""Local-first cancellation and explicit purge of connector-derived delivery payloads."""

from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import KnowledgeSource
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


async def disable(db: AsyncSession, account_id: UUID, *, purge: bool) -> None:
    source_projects = (
        select(ProofContract.project_id)
        .join(KnowledgeSource, KnowledgeSource.id == ProofContract.source_id)
        .where(KnowledgeSource.connector_account_id == account_id)
    )
    evidence_projects = select(ProofEvidence.project_id).where(ProofEvidence.account_id == account_id)
    projects = list(
        await db.scalars(
            select(ProofProject)
            .where(
                or_(
                    ProofProject.configuration["github_account_id"].astext == str(account_id),
                    ProofProject.id.in_(source_projects),
                    ProofProject.id.in_(evidence_projects),
                )
            )
            .order_by(ProofProject.id)
            .with_for_update()
        )
    )
    for project in projects:
        await db.execute(
            update(ProofJob)
            .where(ProofJob.project_id == project.id, ProofJob.status.in_(["queued", "running"]))
            .values(status="cancelled", run_token=None, error="Connection access was disabled")
        )
        if purge:
            project.configuration = {**project.configuration, "purged": True}
            contract_ids = list(
                await db.scalars(
                    select(ProofContract.id)
                    .join(KnowledgeSource, KnowledgeSource.id == ProofContract.source_id)
                    .where(ProofContract.project_id == project.id, KnowledgeSource.connector_account_id == account_id)
                )
            )
            await db.execute(update(ProofContract).where(ProofContract.id.in_(contract_ids)).values(snapshot={}))
            await db.execute(
                update(ProofRequirement)
                .where(ProofRequirement.contract_id.in_(contract_ids))
                .values(original_text="Source data purged", proposal={})
            )
            await db.execute(
                update(ProofEvidence)
                .where(ProofEvidence.project_id == project.id, ProofEvidence.account_id == account_id)
                .values(fact={}, authority={})
            )
            await db.execute(
                update(ProofMilestone).where(ProofMilestone.contract_id.in_(contract_ids)).values(specification={})
            )
            await db.execute(
                update(ProofVerification)
                .where(ProofVerification.project_id == project.id)
                .values(snapshot={}, result={})
            )
            await db.execute(
                update(ProofPackage).where(ProofPackage.project_id == project.id).values(internal={}, customer={})
            )
            await db.execute(update(ProofInvoice).where(ProofInvoice.project_id == project.id).values(payload={}))
            await db.execute(update(ProofJob).where(ProofJob.project_id == project.id).values(payload={}, result={}))
            await db.execute(update(ProofApproval).where(ProofApproval.project_id == project.id).values(reason=None))
