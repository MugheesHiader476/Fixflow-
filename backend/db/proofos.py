"""Additive ProofOS domain storage, independent of parsing and historical agent tables."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models import Base, Owned, Timestamped


class ProofProject(Owned, Timestamped, Base):
    __tablename__ = "proof_projects"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    customer_name: Mapped[str] = mapped_column(String(200))
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB)


class ProofContract(Owned, Timestamped, Base):
    __tablename__ = "proof_contracts"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_proof_contract_revision"),
        CheckConstraint("state IN ('DRAFT','APPROVED','SUPERSEDED')", name="ck_proof_contract_state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="DRAFT", server_default="DRAFT")
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)


class ProofMilestone(Owned, Timestamped, Base):
    __tablename__ = "proof_milestones"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_proof_milestone_code"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    contract_id: Mapped[UUID] = mapped_column(ForeignKey("proof_contracts.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    specification: Mapped[dict[str, object]] = mapped_column(JSONB)
    scope_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    scope_hash: Mapped[str | None] = mapped_column(String(64))


class ProofRequirement(Owned, Timestamped, Base):
    __tablename__ = "proof_requirements"
    __table_args__ = (
        UniqueConstraint("contract_id", "stable_id", name="uq_proof_requirement_stable"),
        CheckConstraint("state IN ('NEEDS_REVIEW','APPROVED')", name="ck_proof_requirement_state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    contract_id: Mapped[UUID] = mapped_column(ForeignKey("proof_contracts.id"), index=True)
    milestone_id: Mapped[UUID | None] = mapped_column(ForeignKey("proof_milestones.id"), index=True)
    stable_id: Mapped[str] = mapped_column(String(60))
    original_text: Mapped[str] = mapped_column(Text)
    proposal: Mapped[dict[str, object]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="NEEDS_REVIEW", server_default="NEEDS_REVIEW")


class ProofEvidence(Owned, Base):
    __tablename__ = "proof_evidence"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    milestone_id: Mapped[UUID] = mapped_column(ForeignKey("proof_milestones.id"), index=True)
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="SET NULL"), index=True)
    identity_hash: Mapped[str] = mapped_column(String(64))
    authority: Mapped[dict[str, object]] = mapped_column(JSONB)
    fact: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofVerification(Owned, Base):
    __tablename__ = "proof_verifications"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    milestone_id: Mapped[UUID] = mapped_column(ForeignKey("proof_milestones.id"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    result: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofPackage(Owned, Base):
    __tablename__ = "proof_packages"
    __table_args__ = (UniqueConstraint("milestone_id", "version", name="uq_proof_package_version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    milestone_id: Mapped[UUID] = mapped_column(ForeignKey("proof_milestones.id"), index=True)
    verification_id: Mapped[UUID] = mapped_column(ForeignKey("proof_verifications.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    internal_hash: Mapped[str] = mapped_column(String(64))
    customer_hash: Mapped[str] = mapped_column(String(64))
    internal: Mapped[dict[str, object]] = mapped_column(JSONB)
    customer: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofApproval(Owned, Base):
    __tablename__ = "proof_approvals"
    __table_args__ = (
        UniqueConstraint("package_id", "kind", name="uq_proof_approval_kind"),
        CheckConstraint("kind IN ('internal','customer')", name="ck_proof_approval_kind"),
        CheckConstraint(
            "status IN ('pending','approved','accepted','rejected','rework_requested')", name="ck_proof_approval_status"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    package_id: Mapped[UUID] = mapped_column(ForeignKey("proof_packages.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(200), index=True)
    package_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    reason: Mapped[str | None] = mapped_column(String(2000))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofInvoice(Owned, Base):
    __tablename__ = "proof_invoices"
    __table_args__ = (UniqueConstraint("milestone_id", name="uq_proof_invoice_milestone"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    milestone_id: Mapped[UUID] = mapped_column(ForeignKey("proof_milestones.id"), index=True)
    package_id: Mapped[UUID] = mapped_column(ForeignKey("proof_packages.id"), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofJob(Owned, Timestamped, Base):
    __tablename__ = "proof_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "intent_hash", name="uq_proof_job_intent"),
        CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="ck_proof_job_status"),
        CheckConstraint("kind IN ('extract','collect','source_evidence','verify','package')", name="ck_proof_job_kind"),
        Index("ix_proof_jobs_pending", "status", "available_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    intent_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    result: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    run_token: Mapped[UUID | None] = mapped_column()
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))


class ProofAudit(Owned, Base):
    __tablename__ = "proof_audit"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("proof_projects.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(200))
    event: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
