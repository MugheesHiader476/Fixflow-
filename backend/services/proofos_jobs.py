"""Bounded durable jobs with persisted claims, immutable results and post-I/O fencing."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from asyncpg import PostgresError
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.core import ConnectorError, record, records, strings
from backend.db.models import AgentRun, ConnectorAccount, ConnectorResource, KnowledgeSource
from backend.db.proofos import (
    ProofContract,
    ProofEvidence,
    ProofJob,
    ProofMilestone,
    ProofPackage,
    ProofProject,
    ProofRequirement,
    ProofVerification,
)
from backend.db.session import get_session_factory
from backend.repositories.parsed import parsed_source
from backend.schemas.agent import ReadProvider, RunRequest
from backend.schemas.github_evidence import GitHubEvidenceRequest, GitHubEvidenceSnapshot, GitHubExpectedCase
from backend.schemas.pipeline import digest
from backend.schemas.verification import Criterion, EvidenceFact, VerificationResult
from backend.security.policy import POLICY_VERSION, validate_disclosure
from backend.security.runtime import configured_secrets, request_policy
from backend.services.acceptance_package import customer_package, render_package
from backend.services.proofos import audit, evidence_fingerprint, latest_verification
from backend.services.proofos_access import (
    account_access,
    contract_current,
    evidence_access,
    owned,
    project_access,
    workspace,
)
from backend.services.proofos_extraction import proposals
from backend.services.verification import verify

logger = logging.getLogger(__name__)


@dataclass
class Product:
    data: dict[str, object]
    source_hashes: dict[UUID, str] = field(default_factory=dict)
    account_hashes: dict[UUID, str] = field(default_factory=dict)


async def origin_authority(db: AsyncSession, job: ProofJob) -> None:
    identifier = job.payload.get("origin_agent_run_id")
    if not identifier:
        return
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == UUID(str(identifier)), AgentRun.owner_id == job.owner_id)
    )
    if run is None or run.status in {"cancelled", "failed"} or run.scope.get("policy_version") != POLICY_VERSION:
        raise HTTPException(409, "Originating chat authority is no longer valid")
    request_policy(RunRequest.model_validate(run.request).question)
    captured = records(job.payload.get("origin_accounts", []))
    if digest(captured) != digest(run.scope.get("accounts", [])):
        raise HTTPException(409, "Originating connection scope differs")
    for reference in captured:
        account = await account_access(db, job.owner_id, UUID(str(reference["id"])))
        if digest(account.configuration) != reference.get("selection_hash"):
            raise HTTPException(409, "Originating connection selection changed")
    source_id = job.payload.get("source_id")
    if source_id:
        source = await db.get(KnowledgeSource, UUID(str(source_id)))
        if (
            source
            and source.connector_account_id
            and str(source.connector_account_id) not in {str(value["id"]) for value in captured}
        ):
            raise HTTPException(409, "Source was outside the originating connection scope")


def criterion_list(milestone: ProofMilestone) -> list[Criterion]:
    if not milestone.scope_approved or digest(milestone.specification) != milestone.scope_hash:
        raise HTTPException(409, "Milestone scope requires approval")
    return [Criterion.model_validate(value) for value in records(milestone.specification.get("criteria", []))]


def current_evidence(evidence: list[ProofEvidence]) -> list[ProofEvidence]:
    selected: dict[str, list[tuple[ProofEvidence, EvidenceFact]]] = {}
    for row in evidence:
        fact = EvidenceFact.model_validate(row.fact)
        identity = (
            digest([fact.kind, fact.repository_id, fact.sha, fact.workflow_id, fact.suite, fact.environment])
            if fact.provider == "github"
            else digest([fact.kind, fact.source_id, sorted(fact.requirement_ids)])
        )
        selected.setdefault(identity, []).append((row, fact))
    result: list[ProofEvidence] = []
    for candidates in selected.values():
        latest_row, latest = max(
            candidates,
            key=lambda value: (value[1].event_at or value[1].observed_at, value[1].run_id or 0, value[1].observed_at),
        )
        same_event = (
            {
                value.id: row
                for row, value in candidates
                if (value.event_at == latest.event_at and value.run_id == latest.run_id)
            }
            if latest.provider == "github"
            else {latest.id: latest_row}
        )
        result.extend(same_event.values())
    return result


def current_facts(evidence: list[ProofEvidence]) -> list[EvidenceFact]:
    return [EvidenceFact.model_validate(row.fact) for row in current_evidence(evidence)]


def github_facts(snapshot: GitHubEvidenceSnapshot, criteria: list[Criterion]) -> list[EvidenceFact]:
    run = snapshot.run
    identity = {
        "provider": "github",
        "repository_id": snapshot.repository_id,
        "branch": snapshot.branch,
        "sha": snapshot.sha,
        "workflow_id": run.workflow_id,
        "run_id": run.id,
        "observed_at": snapshot.observed_at,
        "event_at": run.updated_at,
        "url": run.html_url,
        "verified_native": True,
        "customer_safe": True,
    }
    failed = run.conclusion in {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
    failed = failed or any(
        job.conclusion in {"failure", "timed_out", "action_required", "startup_failure"} for job in snapshot.jobs
    )
    failed = failed or any(
        check.conclusion in {"failure", "timed_out", "action_required", "startup_failure"} for check in snapshot.checks
    )
    facts: list[EvidenceFact] = []
    suites = sorted(
        {case.suite for case in snapshot.test_results} | {criterion.suite for criterion in criteria if criterion.suite}
    )
    selected_suites: list[str | None] = list(suites) if suites else [None]
    for suite in selected_suites:
        results = [case for case in snapshot.test_results if case.suite == suite]
        status = (
            "failed"
            if failed or any(case.status in {"failed", "error"} for case in results)
            else (
                "skipped"
                if any(case.status == "skipped" for case in results)
                else "passed"
                if run.status == "completed" and run.conclusion == "success" and results
                else "unknown"
            )
        )
        facts.append(
            EvidenceFact.model_validate(
                {
                    **identity,
                    "id": "gh-" + digest([snapshot.model_dump(mode="json"), suite])[:40],
                    "kind": "ci",
                    "suite": suite,
                    "cases": [case.name for case in results if case.status == "passed"],
                    "status": status,
                    "title": f"GitHub Actions run {run.id}: {suite or 'test report unavailable'}",
                    "details": "; ".join(snapshot.limitations)[:4000],
                    "complete": bool(
                        snapshot.coverage.jobs
                        and snapshot.coverage.artifacts
                        and snapshot.test_report_status == "parsed"
                        and results
                    ),
                    "external_id": str(run.id),
                }
            )
        )
    for deployment in snapshot.deployments:
        latest = max(deployment.statuses, key=lambda value: value.created_at) if deployment.statuses else None
        facts.append(
            EvidenceFact.model_validate(
                {
                    **identity,
                    "id": "gh-deploy-" + digest(deployment.model_dump(mode="json"))[:40],
                    "kind": "deployment",
                    "title": "Deployment: " + deployment.environment,
                    "environment": deployment.environment,
                    "external_id": str(deployment.id),
                    "status": "passed"
                    if latest and latest.state == "success"
                    else "failed"
                    if latest and latest.state in {"failure", "error", "inactive"}
                    else "pending",
                    "event_at": latest.created_at if latest else deployment.created_at,
                    "complete": snapshot.coverage.deployments and deployment.statuses_complete,
                    "url": latest.environment_url if latest else None,
                }
            )
        )
    return facts


async def produce(job: ProofJob) -> Product:
    async with get_session_factory()() as db:
        db.info["owner_id"] = job.owner_id
        await origin_authority(db, job)
        project = await owned(db, ProofProject, job.project_id)
        await project_access(db, project, current=job.kind in {"verify", "package"})
        if digest(project.configuration) != job.payload.get("project_hash"):
            raise HTTPException(409, "Project configuration changed")
        payload = job.payload
        if job.kind == "extract":
            source_id = UUID(str(payload["source_id"]))
            parsed = await parsed_source(db, source_id, job.owner_id)
            if (
                parsed is None
                or parsed.canonical.source.sha256 != payload.get("source_hash")
                or parsed.canonical.content_hash != payload.get("canonical_hash")
            ):
                raise HTTPException(409, "Contract source changed before extraction")
            extracted = proposals(parsed)
            return Product(
                {
                    "title": payload["title"],
                    "source_id": str(source_id),
                    "source_version": parsed.canonical.source.version,
                    "requirements": extracted,
                    "source": parsed.canonical.source.model_dump(mode="json"),
                },
                source_hashes={source_id: parsed.canonical.source.sha256},
            )
        milestone = await owned(db, ProofMilestone, UUID(str(payload["milestone_id"])))
        if milestone.project_id != project.id:
            raise HTTPException(404, "Milestone not found in this project")
        criteria = criterion_list(milestone)
        await contract_current(db, milestone.contract_id)
        if payload.get("scope_hash") != milestone.scope_hash:
            raise HTTPException(409, "Approved scope changed")
        if job.kind == "source_evidence":
            source_id = UUID(str(payload["source_id"]))
            parsed = await parsed_source(db, source_id, job.owner_id)
            if parsed is None or parsed.canonical.source.sha256 != payload["source_hash"]:
                raise HTTPException(409, "Evidence source changed")
            source = await db.get(KnowledgeSource, source_id)
            if source is None:
                raise HTTPException(404, "Evidence source not found")
            requirements = list(
                await db.scalars(
                    select(ProofRequirement).where(
                        ProofRequirement.id.in_([UUID(value) for value in strings(payload["requirement_ids"])]),
                        ProofRequirement.milestone_id == milestone.id,
                    )
                )
            )
            if len(requirements) != len(strings(payload["requirement_ids"])):
                raise HTTPException(422, "Evidence must map to requirements in this milestone")
            criterion_ids = [
                str(value["id"])
                for requirement in requirements
                for value in records(requirement.proposal["acceptance_criteria"])
            ]
            kind = str(payload["kind"])
            account_hashes: dict[UUID, str] = {}
            authority: dict[str, object] = {"source_hash": source.file_hash}
            if source.connector_account_id:
                account = await account_access(db, job.owner_id, source.connector_account_id)
                account_hashes[account.id] = digest(account.configuration)
                resource = await db.scalar(select(ConnectorResource).where(ConnectorResource.source_id == source.id))
                if resource is None:
                    raise HTTPException(409, "Native source reference is unavailable")
                external = resource.external_id
                path = None
                resource_id = external
                if account.provider == "github" and ":code:" in external:
                    resource_id, path = external.split(":code:", 1)
                authority.update(
                    {
                        "account_id": str(account.id),
                        "selection_hash": account_hashes[account.id],
                        "external_version": resource.external_version,
                        "read_request": ReadProvider(
                            account_id=account.id, resource_id=resource_id, path=path
                        ).model_dump(mode="json"),
                    }
                )
            fact = EvidenceFact.model_validate(
                {
                    "id": "src-" + digest([str(source_id), source.file_hash, criterion_ids, kind])[:40],
                    "kind": kind,
                    "provider": "canonical",
                    "title": source.name,
                    "status": "passed" if kind == "document" else "unknown",
                    "observed_at": datetime.now(UTC),
                    "source_id": str(source_id),
                    "source_hash": source.file_hash,
                    "external_id": str(source_id),
                    "complete": True,
                    "verified_native": bool(source.connector_account_id),
                    "customer_safe": bool(payload["customer_safe"]),
                    "requirement_ids": criterion_ids,
                    "details": "\n\n".join(block.content for block in parsed.canonical.blocks)[:4000],
                }
            )
            product = Product(
                {
                    "facts": [fact.model_dump(mode="json")],
                    "authority": authority,
                    "account_id": str(source.connector_account_id) if source.connector_account_id else None,
                    "source_id": str(source.id),
                },
                {source_id: source.file_hash},
                account_hashes,
            )
        elif job.kind == "collect":
            identifier = UUID(str(project.configuration["github_account_id"]))
            account = await account_access(db, job.owner_id, identifier)
            captured = digest(account.configuration)
            request = GitHubEvidenceRequest(
                repository_id=str(project.configuration["repository_id"]),
                branch=str(project.configuration["branch"]),
                sha=str(milestone.specification["expected_sha"]),
                run_id=int(str(payload["run_id"])),
                workflow_id=int(str(payload["workflow_id"])),
                expected_suites=sorted({criterion.suite for criterion in criteria if criterion.suite}),
                expected_cases=[
                    GitHubExpectedCase(suite=criterion.suite, name=name)
                    for criterion in criteria
                    if criterion.suite
                    for name in criterion.cases
                ],
                artifact_names=strings(payload.get("artifact_names", [])),
            )
            native_workspace = workspace(account)
            await db.commit()
            snapshot = await native_workspace.github_evidence(request)
            if snapshot.repository_name != project.configuration.get("repository_name"):
                raise HTTPException(409, "Selected repository identity changed")
            facts = github_facts(snapshot, criteria)
            product = Product(
                {
                    "facts": [fact.model_dump(mode="json") for fact in facts],
                    "authority": {
                        "account_id": str(identifier),
                        "selection_hash": captured,
                        "repository_id": request.repository_id,
                        "request": request.model_dump(mode="json"),
                        "snapshot": snapshot.model_dump(mode="json"),
                    },
                    "account_id": str(identifier),
                    "source_id": None,
                },
                account_hashes={identifier: captured},
            )
        elif job.kind in {"verify", "package"}:
            evidence = list(
                await db.scalars(
                    select(ProofEvidence)
                    .where(ProofEvidence.milestone_id == milestone.id, ProofEvidence.owner_id == job.owner_id)
                    .order_by(ProofEvidence.created_at, ProofEvidence.id)
                    .limit(501)
                )
            )
            if len(evidence) > 500:
                raise HTTPException(409, "Evidence review limit reached; create a new milestone scope")
            active_evidence = current_evidence(evidence)
            for row in active_evidence:
                await evidence_access(db, row)
            evidence_hash = digest([row.identity_hash for row in evidence])
            if payload.get("evidence_hash") != evidence_hash:
                raise HTTPException(409, "Evidence changed; start a new verification")
            facts = [EvidenceFact.model_validate(row.fact) for row in active_evidence]
            authority_accounts = {
                row.account_id: str(row.authority["selection_hash"]) for row in active_evidence if row.account_id
            }
            source_hashes = {
                row.source_id: str(row.authority["source_hash"]) for row in active_evidence if row.source_id
            }
            if job.kind == "verify":
                result = verify(criteria, facts, approved=True, now=datetime.now(UTC))
                product = Product(
                    {
                        "result": result.model_dump(mode="json"),
                        "snapshot": {
                            "scope_hash": milestone.scope_hash,
                            "evidence_hash": evidence_hash,
                            "criteria": [value.model_dump(mode="json") for value in criteria],
                            "evidence_ids": [str(value.id) for value in active_evidence],
                            "facts": [value.model_dump(mode="json") for value in facts],
                        },
                    },
                    source_hashes,
                    authority_accounts,
                )
            else:
                verification = await latest_verification(db, milestone)
                if verification is None or str(verification.id) != payload.get("verification_id"):
                    raise HTTPException(409, "A current verification is required")
                if verification.snapshot.get("evidence_hash") != evidence_hash:
                    raise HTTPException(409, "Reverify changed evidence before packaging")
                contract = await owned(db, ProofContract, milestone.contract_id)
                requirements = list(
                    await db.scalars(select(ProofRequirement).where(ProofRequirement.milestone_id == milestone.id))
                )
                package_number = (
                    await db.scalar(
                        select(func.max(ProofPackage.version)).where(ProofPackage.milestone_id == milestone.id)
                    )
                    or 0
                ) + 1
                report = {
                    "brand": "ProofOS",
                    "project": {"name": project.name, "customer_name": project.customer_name},
                    "milestone": {
                        "code": milestone.code,
                        "title": milestone.title,
                        "description": milestone.specification.get("description", ""),
                    },
                    "contract": {
                        "title": contract.title,
                        "revision": contract.revision,
                        "source_hash": contract.source_hash,
                        "source_version": contract.source_version,
                    },
                    "verification": verification.result,
                    "requirements": [
                        {
                            "stable_id": value.stable_id,
                            "original_text": value.original_text,
                            "interpretation": value.proposal.get("interpretation"),
                            "acceptance_criteria": value.proposal.get("acceptance_criteria"),
                        }
                        for value in requirements
                    ],
                    "evidence": [value.model_dump(mode="json") for value in facts],
                    "exceptions": [
                        check.explanation
                        for check in VerificationResult.model_validate(verification.result).checks
                        if check.status not in {"PASSED", "NOT_APPLICABLE"}
                    ],
                    "internal_review": {"status": "pending"},
                    "customer_acceptance": {"status": "pending"},
                    "generated_at": datetime.now(UTC).isoformat(),
                    "package_version": package_number,
                }
                customer = customer_package(report)
                validate_disclosure(customer, configured_secrets())
                await asyncio.to_thread(render_package, customer)
                product = Product(
                    {
                        "internal": report,
                        "customer": customer,
                        "version": package_number,
                        "verification_id": str(verification.id),
                    },
                    source_hashes,
                    authority_accounts,
                )
            await db.commit()
            for row in active_evidence:
                await evidence_access(db, row, native=True)
        else:
            raise ValueError("Unsupported ProofOS job")
        if job.kind == "source_evidence":
            temporary = ProofEvidence(
                owner_id=job.owner_id,
                source_id=UUID(str(product.data["source_id"])),
                account_id=UUID(str(product.data["account_id"])) if product.data.get("account_id") else None,
                authority=record(product.data["authority"]),
            )
            await db.commit()
            await evidence_access(db, temporary, native=True)
        return product


async def persist(db: AsyncSession, job: ProofJob, product: Product, token: UUID) -> None:
    for identifier, captured in sorted(product.account_hashes.items()):
        account = await db.get(ConnectorAccount, identifier, with_for_update=True, populate_existing=True)
        if (
            account is None
            or account.owner_id != job.owner_id
            or account.status not in {"connected", "syncing", "connected_with_warning"}
            or account.authentication_status != "valid"
            or not account.credential_reference
            or digest(account.configuration) != captured
        ):
            raise HTTPException(409, "Connection authority changed during collection")
    project = await owned(db, ProofProject, job.project_id, lock=True)
    current = await owned(db, ProofJob, job.id, lock=True)
    if (
        current.status != "running"
        or current.run_token != token
        or not current.lease_until
        or current.lease_until <= datetime.now(UTC)
    ):
        raise HTTPException(409, "Job claim expired or was cancelled")
    await origin_authority(db, job)
    await project_access(db, project, current=job.kind in {"verify", "package"})
    if job.kind != "extract":
        applicable = await owned(db, ProofMilestone, UUID(str(job.payload["milestone_id"])))
        await contract_current(db, applicable.contract_id)
    if job.kind in {"verify", "package"}:
        milestone = await owned(db, ProofMilestone, UUID(str(job.payload["milestone_id"])))
        criterion_list(milestone)
        if job.payload.get("scope_hash") != milestone.scope_hash or job.payload.get(
            "evidence_hash"
        ) != await evidence_fingerprint(db, milestone.id):
            raise HTTPException(409, "Verification inputs changed during processing")
        if job.kind == "package":
            latest = await latest_verification(db, milestone)
            if latest is None or str(latest.id) != job.payload.get("verification_id"):
                raise HTTPException(409, "A newer verification superseded this package request")
    if digest(project.configuration) != job.payload.get("project_hash"):
        raise HTTPException(409, "Project configuration changed during processing")
    for source_id, source_hash in product.source_hashes.items():
        parsed = await parsed_source(db, source_id, job.owner_id)
        if parsed is None or parsed.canonical.source.sha256 != source_hash:
            raise HTTPException(409, "Source authority changed during processing")
    data = product.data
    result: dict[str, object]
    if job.kind == "extract":
        revision = (
            await db.scalar(select(func.max(ProofContract.revision)).where(ProofContract.project_id == project.id)) or 0
        ) + 1
        contract = ProofContract(
            owner_id=job.owner_id,
            project_id=project.id,
            source_id=UUID(str(data["source_id"])),
            title=str(data["title"]),
            revision=revision,
            source_hash=next(iter(product.source_hashes.values())),
            source_version=int(str(data["source_version"])),
            snapshot={"source": data["source"], "extraction": "canonical-rules-v1"},
        )
        db.add(contract)
        await db.flush()
        for proposal in records(data["requirements"]):
            db.add(
                ProofRequirement(
                    owner_id=job.owner_id,
                    project_id=project.id,
                    contract_id=contract.id,
                    stable_id=str(proposal["stable_id"]),
                    original_text=str(proposal["original_text"]),
                    proposal={
                        key: value for key, value in proposal.items() if key not in {"stable_id", "original_text"}
                    },
                )
            )
        result = {"contract_id": str(contract.id), "requirements": len(records(data["requirements"]))}
    elif job.kind in {"collect", "source_evidence"}:
        result_ids: list[str] = []
        for fact in records(data["facts"]):
            identity = digest(fact)
            row = ProofEvidence(
                owner_id=job.owner_id,
                project_id=project.id,
                milestone_id=UUID(str(job.payload["milestone_id"])),
                account_id=UUID(str(data["account_id"])) if data.get("account_id") else None,
                source_id=UUID(str(data["source_id"])) if data.get("source_id") else None,
                identity_hash=identity,
                authority=record(data["authority"]),
                fact=fact,
            )
            db.add(row)
            await db.flush()
            result_ids.append(str(row.id))
        result = {"evidence_ids": result_ids}
    elif job.kind == "verify":
        value = VerificationResult.model_validate(data["result"])
        verification_row = ProofVerification(
            owner_id=job.owner_id,
            project_id=project.id,
            milestone_id=UUID(str(job.payload["milestone_id"])),
            input_hash=value.input_hash,
            snapshot=record(data["snapshot"]),
            result=value.model_dump(mode="json"),
        )
        db.add(verification_row)
        await db.flush()
        result = {"verification_id": str(verification_row.id)}
    else:
        row_package = ProofPackage(
            owner_id=job.owner_id,
            project_id=project.id,
            milestone_id=UUID(str(job.payload["milestone_id"])),
            verification_id=UUID(str(data["verification_id"])),
            version=int(str(data["version"])),
            internal=record(data["internal"]),
            customer=record(data["customer"]),
            internal_hash=digest(data["internal"]),
            customer_hash=digest(data["customer"]),
        )
        db.add(row_package)
        await db.flush()
        result = {"package_id": str(row_package.id)}
    current.result, current.status, current.run_token, current.lease_until = result, "completed", None, None
    audit(db, project, "job_completed", job_id=str(job.id), kind=job.kind, **result)


async def process_job(identifier: UUID) -> None:
    token = uuid4()
    async with get_session_factory().begin() as db:
        job = await db.scalar(select(ProofJob).where(ProofJob.id == identifier).with_for_update(skip_locked=True))
        now = datetime.now(UTC)
        if (
            job is None
            or job.status not in {"queued", "running"}
            or job.available_at > now
            or (job.status == "running" and job.lease_until and job.lease_until > now)
        ):
            return
        if job.attempts >= 3 or job.created_at < now - timedelta(hours=1):
            job.status, job.error = "failed", "Job exceeded its bounded attempts"
            return
        job.status, job.run_token, job.lease_until, job.attempts = (
            "running",
            token,
            now + timedelta(seconds=180),
            job.attempts + 1,
        )
    try:
        async with asyncio.timeout(150):
            product = await produce(job)
        async with get_session_factory().begin() as db:
            db.info["owner_id"] = job.owner_id
            await persist(db, job, product, token)
    except (HTTPException, ConnectorError, ValueError, OSError, TimeoutError, SQLAlchemyError, PostgresError) as error:
        async with get_session_factory().begin() as db:
            current = await db.get(ProofJob, identifier, with_for_update=True)
            if current is not None and current.run_token == token and current.status == "running":
                retry = isinstance(error, ConnectorError) and error.code == "rate_limited"
                if retry:
                    current.attempts -= 1
                current.status = "queued" if retry else "failed"
                current.error = (
                    "Provider rate limit; job will resume"
                    if retry
                    else "ProofOS processing failed; check source access, approved scope and provider evidence"
                )
                delay = error.retry_after or 60 if isinstance(error, ConnectorError) else 0
                current.available_at = (
                    datetime.now(UTC) + timedelta(seconds=min(3600, max(2, delay))) if retry else datetime.now(UTC)
                )
                current.run_token, current.lease_until = None, None
        logger.warning("ProofOS job %s stopped (%s)", identifier, type(error).__name__)


async def worker() -> None:
    while True:
        try:
            async with get_session_factory()() as db:
                now = datetime.now(UTC)
                identifiers = list(
                    await db.scalars(
                        select(ProofJob.id)
                        .where(
                            ProofJob.status.in_(["queued", "running"]),
                            ProofJob.available_at <= now,
                            or_(ProofJob.lease_until.is_(None), ProofJob.lease_until <= now),
                        )
                        .order_by(ProofJob.created_at)
                        .limit(2)
                    )
                )
            await asyncio.gather(*(process_job(identifier) for identifier in identifiers))
        except (SQLAlchemyError, PostgresError, OSError, ValueError) as error:
            logger.warning("ProofOS queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(2)
