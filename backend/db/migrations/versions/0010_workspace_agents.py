"""Durable owner-scoped Grok workflows, reviewed email outbox and private file tickets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import SchemaItem

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def common() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.String(200), nullable=False, server_default="__legacy__"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        *common(),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("messages", JSONB(), nullable=False, server_default="[]"),
        sa.Column("pending_calls", JSONB(), nullable=False, server_default="[]"),
        sa.Column("materials", JSONB(), nullable=False, server_default="[]"),
        sa.Column("answer", JSONB()),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage", JSONB(), nullable=False, server_default="{}"),
        sa.Column("run_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("error", sa.String(300)),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')", name="ck_agent_run_status"
        ),
    )
    op.create_index("ix_agent_runs_owner_id", "agent_runs", ["owner_id"])
    op.create_index("ix_agent_runs_pending", "agent_runs", ["status", "next_attempt_at"])
    op.create_table(
        "agent_actions",
        *common(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("connector_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("intent_hash", sa.String(64), nullable=False),
        sa.Column("provider_message_id", sa.String(200)),
        sa.Column("error", sa.String(300)),
        sa.UniqueConstraint("run_id", "payload_hash", name="uq_agent_action_payload"),
        sa.UniqueConstraint("run_id", "intent_hash", name="uq_agent_action_intent"),
        sa.CheckConstraint(
            "status IN ('draft','dispatching','sent','blocked','unknown')", name="ck_agent_action_status"
        ),
    )
    op.create_table(
        "agent_files",
        *common(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("connector_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reference", JSONB(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.UniqueConstraint("run_id", "request_hash", name="uq_agent_file_request"),
    )
    for table in ["agent_actions", "agent_files"]:
        for field in ["owner_id", "run_id", "account_id"]:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM agent_runs)")):
        raise RuntimeError("Cannot downgrade while agent history exists; export and explicitly remove it first")
    op.drop_table("agent_files")
    op.drop_table("agent_actions")
    op.drop_table("agent_runs")
