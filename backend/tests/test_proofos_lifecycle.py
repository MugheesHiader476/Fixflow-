import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ConnectorAccount, KnowledgeSource, SavedSolution
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
from backend.schemas.verification import EvidenceFact
from backend.services.proofos_jobs import current_evidence
from backend.services.proofos_lifecycle import disable

pytestmark = pytest.mark.anyio


def test_current_evidence_preserves_environment_and_same_event_conflicts() -> None:
    now = datetime.now(UTC)

    def row(status: Literal["passed", "failed"], environment: str, age: int, run: int) -> ProofEvidence:
        fact = EvidenceFact(
            id=str(uuid4()),
            kind="deployment",
            provider="github",
            status=status,
            repository_id="42",
            sha="a" * 40,
            workflow_id=7,
            run_id=run,
            environment=environment,
            observed_at=now,
            event_at=now - timedelta(minutes=age),
            title="Deployment",
        )
        return ProofEvidence(id=uuid4(), fact=fact.model_dump(mode="json"))

    stale_failure = row("failed", "staging", 10, 1)
    new_pass = row("passed", "staging", 1, 2)
    same_event_failure = row("failed", "staging", 1, 2)
    production = row("passed", "production", 2, 2)
    selected = current_evidence([production, new_pass, stale_failure, same_event_failure])
    assert {item.id for item in selected} == {new_pass.id, same_event_failure.id, production.id}


async def test_connector_purge_redacts_derived_payloads_and_preserves_independent_uploads(db: AsyncSession) -> None:
    account = ConnectorAccount(
        owner_id="user_test", provider="github", external_account_id="42", display_name="Synthetic"
    )
    db.add(account)
    await db.flush()
    project = ProofProject(
        owner_id="user_test",
        name="Test",
        customer_name="Fictional",
        configuration={"github_account_id": str(account.id)},
    )
    db.add(project)
    await db.flush()
    remote = KnowledgeSource(
        owner_id="user_test", name="remote", file_hash="a" * 64, source_type="github", connector_account_id=account.id
    )
    upload = KnowledgeSource(owner_id="user_test", name="upload", file_hash="b" * 64, source_type="upload")
    db.add_all([remote, upload])
    await db.flush()
    remote_contract = ProofContract(
        owner_id="user_test",
        project_id=project.id,
        source_id=remote.id,
        title="Remote",
        revision=1,
        source_hash=remote.file_hash,
        source_version=1,
        snapshot={"private": "remote contract"},
    )
    upload_contract = ProofContract(
        owner_id="user_test",
        project_id=project.id,
        source_id=upload.id,
        title="Upload",
        revision=2,
        source_hash=upload.file_hash,
        source_version=1,
        snapshot={"private": "independent upload"},
    )
    db.add_all([remote_contract, upload_contract])
    await db.flush()
    requirements = [
        ProofRequirement(
            owner_id="user_test",
            project_id=project.id,
            contract_id=contract.id,
            stable_id="REQ-001",
            original_text=wording,
            proposal={"interpretation": wording},
        )
        for contract, wording in [
            (remote_contract, "private remote wording"),
            (upload_contract, "independent uploaded wording"),
        ]
    ]
    milestone = ProofMilestone(
        owner_id="user_test",
        project_id=project.id,
        contract_id=remote_contract.id,
        code="M1",
        title="Delivery",
        specification={"criteria": ["private remote wording"]},
    )
    db.add_all([*requirements, milestone])
    await db.flush()
    verification = ProofVerification(
        owner_id="user_test",
        project_id=project.id,
        milestone_id=milestone.id,
        input_hash="c" * 64,
        snapshot={"private": "remote"},
        result={"private": "remote"},
    )
    db.add(verification)
    await db.flush()
    package = ProofPackage(
        owner_id="user_test",
        project_id=project.id,
        milestone_id=milestone.id,
        verification_id=verification.id,
        version=1,
        internal_hash="d" * 64,
        customer_hash="e" * 64,
        internal={"private": "remote"},
        customer={"private": "remote"},
    )
    job = ProofJob(
        owner_id="user_test",
        project_id=project.id,
        kind="verify",
        intent_hash="f" * 64,
        payload={"private": "remote"},
        result={"private": "remote"},
    )
    saved = SavedSolution(owner_id="user_test", payload={"independent": "saved history"})
    db.add_all([package, job, saved])
    await db.commit()
    await disable(db, account.id, purge=True)
    await db.commit()
    await db.refresh(remote_contract)
    await db.refresh(upload_contract)
    for requirement in requirements:
        await db.refresh(requirement)
    await db.refresh(milestone)
    await db.refresh(package)
    await db.refresh(job)
    assert remote_contract.snapshot == {} and upload_contract.snapshot == {"private": "independent upload"}
    assert requirements[0].original_text == "Source data purged"
    assert requirements[1].original_text == "independent uploaded wording"
    assert milestone.specification == {} and package.internal == {} and package.customer == {}
    assert job.status == "cancelled" and job.payload == {} and job.result == {}
    assert await db.scalar(select(SavedSolution.payload).where(SavedSolution.id == saved.id)) == {
        "independent": "saved history"
    }


async def test_populated_proofos_migration_refuses_downgrade(db: AsyncSession, migrated_database: str) -> None:
    project = ProofProject(owner_id="user_test", name="Retained", customer_name="Fictional", configuration={})
    db.add(project)
    await db.commit()
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "downgrade", "0010"],
        env={**os.environ, "DATABASE_URL": migrated_database},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0 and "Cannot downgrade populated ProofOS history" in result.stderr
    assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "0011"
    assert await db.get(ProofProject, project.id) is not None
