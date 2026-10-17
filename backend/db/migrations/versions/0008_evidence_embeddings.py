"""Optional source-scoped Gemini vectors; no backfill or legacy vector changes."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evidence_passages", sa.Column("embedding_input_hash", sa.String(64), nullable=True))
    op.create_table(
        "evidence_embeddings",
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("passage_indexes.source_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("profile", sa.String(100), primary_key=True),
        sa.Column("input_hash", sa.String(64), primary_key=True),
        sa.Column("embedding", Vector(768), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM evidence_embeddings)")):
        raise RuntimeError("Cannot downgrade while evidence embeddings exist; explicitly remove derived indexes first")
    op.drop_table("evidence_embeddings")
    op.drop_column("evidence_passages", "embedding_input_hash")
