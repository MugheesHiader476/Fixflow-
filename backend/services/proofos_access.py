"""Reauthorize domain-derived content before decoding private snapshots."""

from typing import TypeVar, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError, strings
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.registry import adapter
from backend.connectors.slack import SlackConnector
from backend.connectors.workspace import NativeWorkspace
from backend.db.models import ConnectorAccount, ConnectorResource, KnowledgeSource
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
from backend.repositories.source_access import accessible_source
from backend.schemas.agent import ReadProvider
from backend.schemas.connectors import Provider, Selection
from backend.schemas.pipeline import digest
from backend.services.access import owner_id

OwnedModel = TypeVar(
    "OwnedModel",
    bound=ProofProject
    | ProofContract
    | ProofMilestone
    | ProofRequirement
    | ProofEvidence
    | ProofVerification
    | ProofPackage
    | ProofApproval
    | ProofInvoice
    | ProofJob,
)
ACTIVE = ("connected", "syncing", "connected_with_warning")


async def owned(db: AsyncSession, model: type[OwnedModel], identifier: UUID, *, lock: bool = False) -> OwnedModel:
    query = select(model).where(model.id == identifier, model.owner_id == owner_id(db))
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "ProofOS record not found")
    return row


async def project_access(db: AsyncSession, project: ProofProject, *, current: bool = False) -> None:
    if project.configuration.get("purged"):
        raise HTTPException(409, "Project source data was purged; create a new project scope")
    references = list(
        await db.execute(
            select(ProofContract.source_id, ProofContract.source_hash).where(ProofContract.project_id == project.id)
        )
    )
    references.extend(
        await db.execute(
            select(ProofEvidence.source_id, ProofEvidence.identity_hash).where(
                ProofEvidence.project_id == project.id, ProofEvidence.source_id.is_not(None)
            )
        )
    )
    for source_id, _ in references:
        if (
            source_id is None
            or await db.scalar(
                select(KnowledgeSource.id).where(
                    KnowledgeSource.id == source_id,
                    KnowledgeSource.owner_id == project.owner_id,
                    KnowledgeSource.status == "parsed",
                    accessible_source(current_time=True),
                )
            )
            is None
        ):
            raise HTTPException(409, "Project source access is unavailable; reconnect or review its scope")
    account_ids = {
        value
        for value in await db.scalars(
            select(ProofEvidence.account_id).where(
                ProofEvidence.project_id == project.id, ProofEvidence.account_id.is_not(None)
            )
        )
        if value is not None
    }
    configured = project.configuration.get("github_account_id")
    if configured:
        account_ids.add(UUID(str(configured)))
    for account_id in account_ids:
        await account_access(db, project.owner_id, account_id)


async def contract_current(db: AsyncSession, contract_id: UUID) -> None:
    contract = await db.get(ProofContract, contract_id, populate_existing=True)
    source = (
        await db.get(KnowledgeSource, contract.source_id, populate_existing=True)
        if contract and contract.source_id
        else None
    )
    if contract is None or contract.state != "APPROVED" or source is None or source.file_hash != contract.source_hash:
        raise HTTPException(409, "Applicable approved contract source changed; review an amendment")


async def account_access(db: AsyncSession, owner: str, identifier: UUID) -> ConnectorAccount:
    account = await db.scalar(
        select(ConnectorAccount)
        .where(
            ConnectorAccount.id == identifier,
            ConnectorAccount.owner_id == owner,
            ConnectorAccount.status.in_(ACTIVE),
            ConnectorAccount.authentication_status == "valid",
            ConnectorAccount.credential_reference.is_not(None),
        )
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise HTTPException(409, "Selected connection is unavailable")
    return account


def workspace(account: ConnectorAccount) -> NativeWorkspace:
    candidate = adapter(cast(Provider, account.provider), get_settings().connectors, account)
    if not isinstance(candidate, (GmailConnector, GoogleDriveConnector, GitHubConnector, SlackConnector)):
        raise HTTPException(409, "Unknown native connector")
    return NativeWorkspace(candidate, Selection.model_validate(account.configuration))


async def evidence_access(
    db: AsyncSession, evidence: ProofEvidence, *, native: bool = False, current: bool = True
) -> None:
    account = None
    if evidence.account_id:
        account = await account_access(db, evidence.owner_id, evidence.account_id)
        if digest(account.configuration) != evidence.authority.get("selection_hash"):
            raise HTTPException(409, "Evidence resource selection changed; collect a new snapshot")
    elif evidence.authority.get("account_id"):
        raise HTTPException(409, "Evidence connection was removed")
    if evidence.source_id:
        source = await db.scalar(
            select(KnowledgeSource)
            .where(
                KnowledgeSource.id == evidence.source_id,
                KnowledgeSource.owner_id == evidence.owner_id,
                KnowledgeSource.status == "parsed",
                accessible_source(current_time=True),
            )
            .execution_options(populate_existing=True)
        )
        if source is None or (current and source.file_hash != evidence.authority.get("source_hash")):
            raise HTTPException(409, "Evidence source access or version changed")
        if native and account:
            resource = await db.scalar(
                select(ConnectorResource)
                .where(
                    ConnectorResource.source_id == source.id,
                    ConnectorResource.account_id == account.id,
                    ConnectorResource.removed_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
            if resource is None or (
                current and resource.external_version != evidence.authority.get("external_version")
            ):
                raise HTTPException(409, "Native evidence version changed")
            args = evidence.authority.get("read_request")
            if not isinstance(args, dict):
                raise HTTPException(409, "Native evidence has no verifiable resource reference")
            native_resource = await workspace(account).resource(ReadProvider.model_validate(args))
            if current and native_resource.version != resource.external_version:
                raise HTTPException(409, "Native evidence version changed; synchronize again")
    elif evidence.authority.get("source_hash"):
        raise HTTPException(409, "Evidence source was removed")
    if native and account and evidence.authority.get("repository_id"):
        try:
            await workspace(account).github_repository(str(evidence.authority["repository_id"]))
        except ConnectorError as error:
            raise HTTPException(409, "Native repository access could not be reverified") from error


async def verification_access(
    db: AsyncSession, verification: ProofVerification, *, native: bool = False, current: bool = True
) -> None:
    for identifier in strings(verification.snapshot.get("evidence_ids", [])):
        evidence = await db.get(ProofEvidence, UUID(identifier), populate_existing=True)
        if (
            evidence is None
            or evidence.owner_id != verification.owner_id
            or evidence.milestone_id != verification.milestone_id
        ):
            raise HTTPException(409, "Verification evidence is unavailable")
        await evidence_access(db, evidence, native=native, current=current)


async def lock_verification_authority(db: AsyncSession, verification: ProofVerification) -> None:
    identifiers = [UUID(value) for value in strings(verification.snapshot.get("evidence_ids", []))]
    account_ids = select(ProofEvidence.account_id).where(
        ProofEvidence.id.in_(identifiers),
        ProofEvidence.owner_id == verification.owner_id,
        ProofEvidence.account_id.is_not(None),
    )
    await db.execute(
        select(ConnectorAccount)
        .where(ConnectorAccount.id.in_(account_ids))
        .order_by(ConnectorAccount.id)
        .with_for_update()
    )
    await verification_access(db, verification)
