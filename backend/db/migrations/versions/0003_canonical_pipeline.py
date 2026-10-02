"""Persist validated canonical/OKF/chunk artifacts without changing existing corpus records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_sources",
        sa.Column("ingestion_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_table(
        "ingestion_artifacts",
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ingestion_artifacts")
    op.drop_column("knowledge_sources", "ingestion_metadata")
