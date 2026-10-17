"""Add opt-in project passage indexes; preserve parsing and all historical data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passage_indexes",
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column("specification", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_passage_indexes_project", "passage_indexes", ["project"])
    op.create_table(
        "evidence_passages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("passage_indexes.source_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "search_text",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', content), 'A') || "
                "setweight(to_tsvector('simple', content), 'A') || "
                "setweight(to_tsvector('english', prefix), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_evidence_source_ordinal"),
    )
    op.create_index("ix_evidence_passages_source_id", "evidence_passages", ["source_id"])
    op.create_index("ix_evidence_search", "evidence_passages", ["search_text"], postgresql_using="gin")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM passage_indexes)")):
        raise RuntimeError("Cannot downgrade while passage indexes exist; explicitly remove derived indexes first")
    op.drop_table("evidence_passages")
    op.drop_table("passage_indexes")
