"""Owner projects, frozen contractual scope, evidence and immutable delivery history."""

from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import SchemaItem

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TABLES = (
    "proof_projects",
    "proof_contracts",
    "proof_milestones",
    "proof_requirements",
    "proof_evidence",
    "proof_verifications",
    "proof_packages",
    "proof_approvals",
    "proof_invoices",
    "proof_jobs",
    "proof_audit",
)


def common(*, updated: bool = False) -> list[SchemaItem]:
    columns: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.String(200), nullable=False, server_default="__legacy__"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]
    if updated:
        columns.append(
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        )
    return columns


def reference(name: str, table: str, *, nullable: bool = False, ondelete: str | None = None) -> sa.Column[UUID]:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(table + ".id", ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "proof_projects",
        *common(updated=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
    )
    op.create_table(
        "proof_contracts",
        *common(updated=True),
        reference("project_id", "proof_projects"),
        reference("source_id", "knowledge_sources", nullable=True, ondelete="SET NULL"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.UniqueConstraint("project_id", "revision", name="uq_proof_contract_revision"),
        sa.CheckConstraint("state IN ('DRAFT','APPROVED','SUPERSEDED')", name="ck_proof_contract_state"),
    )
    op.create_table(
        "proof_milestones",
        *common(updated=True),
        reference("project_id", "proof_projects"),
        reference("contract_id", "proof_contracts"),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("specification", JSONB(), nullable=False),
        sa.Column("scope_approved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("scope_hash", sa.String(64)),
        sa.UniqueConstraint("project_id", "code", name="uq_proof_milestone_code"),
    )
    op.create_table(
        "proof_requirements",
        *common(updated=True),
        reference("project_id", "proof_projects"),
        reference("contract_id", "proof_contracts"),
        reference("milestone_id", "proof_milestones", nullable=True),
        sa.Column("stable_id", sa.String(60), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("proposal", JSONB(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="NEEDS_REVIEW"),
        sa.UniqueConstraint("contract_id", "stable_id", name="uq_proof_requirement_stable"),
        sa.CheckConstraint("state IN ('NEEDS_REVIEW','APPROVED')", name="ck_proof_requirement_state"),
    )
    op.create_table(
        "proof_evidence",
        *common(),
        reference("project_id", "proof_projects"),
        reference("milestone_id", "proof_milestones"),
        reference("account_id", "connector_accounts", nullable=True, ondelete="SET NULL"),
        reference("source_id", "knowledge_sources", nullable=True, ondelete="SET NULL"),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("authority", JSONB(), nullable=False),
        sa.Column("fact", JSONB(), nullable=False),
    )
    op.create_table(
        "proof_verifications",
        *common(),
        reference("project_id", "proof_projects"),
        reference("milestone_id", "proof_milestones"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
    )
    op.create_table(
        "proof_packages",
        *common(),
        reference("project_id", "proof_projects"),
        reference("milestone_id", "proof_milestones"),
        reference("verification_id", "proof_verifications"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("internal_hash", sa.String(64), nullable=False),
        sa.Column("customer_hash", sa.String(64), nullable=False),
        sa.Column("internal", JSONB(), nullable=False),
        sa.Column("customer", JSONB(), nullable=False),
        sa.UniqueConstraint("milestone_id", "version", name="uq_proof_package_version"),
    )
    op.create_table(
        "proof_approvals",
        *common(),
        reference("project_id", "proof_projects"),
        reference("package_id", "proof_packages"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(2000)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("package_id", "kind", name="uq_proof_approval_kind"),
        sa.CheckConstraint("kind IN ('internal','customer')", name="ck_proof_approval_kind"),
        sa.CheckConstraint(
            "status IN ('pending','approved','accepted','rejected','rework_requested')", name="ck_proof_approval_status"
        ),
    )
    op.create_table(
        "proof_invoices",
        *common(),
        reference("project_id", "proof_projects"),
        reference("milestone_id", "proof_milestones"),
        reference("package_id", "proof_packages"),
        sa.Column("payload", JSONB(), nullable=False),
        sa.UniqueConstraint("milestone_id", name="uq_proof_invoice_milestone"),
    )
    op.create_table(
        "proof_jobs",
        *common(updated=True),
        reference("project_id", "proof_projects"),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("intent_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("result", JSONB(), nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("error", sa.String(300)),
        sa.UniqueConstraint("project_id", "intent_hash", name="uq_proof_job_intent"),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')", name="ck_proof_job_status"
        ),
        sa.CheckConstraint(
            "kind IN ('extract','collect','source_evidence','verify','package')", name="ck_proof_job_kind"
        ),
    )
    op.create_index("ix_proof_jobs_pending", "proof_jobs", ["status", "available_at"])
    op.create_table(
        "proof_audit",
        *common(),
        reference("project_id", "proof_projects"),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("details", JSONB(), nullable=False),
    )
    fields = {
        "proof_contracts": ["project_id", "source_id"],
        "proof_milestones": ["project_id", "contract_id"],
        "proof_requirements": ["project_id", "contract_id", "milestone_id"],
        "proof_evidence": ["project_id", "milestone_id", "account_id", "source_id"],
        "proof_verifications": ["project_id", "milestone_id"],
        "proof_packages": ["project_id", "milestone_id", "verification_id"],
        "proof_approvals": ["project_id", "package_id", "subject"],
        "proof_invoices": ["project_id", "milestone_id", "package_id"],
        "proof_jobs": ["project_id"],
        "proof_audit": ["project_id"],
    }
    for table in TABLES:
        for field in ["owner_id", *fields.get(table, [])]:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade() -> None:
    for table in TABLES:
        if op.get_bind().scalar(sa.select(sa.exists().select_from(sa.table(table)))):
            raise RuntimeError("Cannot downgrade populated ProofOS history; explicitly export and remove it first")
    for table in reversed(TABLES):
        op.drop_table(table)
