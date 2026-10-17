"""Versioned vector memberships, atomic activation and durable bounded embedding jobs.

Existing cache rows are preserved but never activated without current validation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("passage_indexes", sa.Column("vector_fingerprint", sa.String(64)))
    op.add_column("passage_indexes", sa.Column("vector_profile", sa.String(200)))
    op.create_unique_constraint("uq_evidence_source_id", "evidence_passages", ["source_id", "id"])
    op.alter_column("evidence_embeddings", "profile", type_=sa.String(200), existing_type=sa.String(100))
    for column in (
        sa.Column("model", sa.String(100), nullable=False, server_default="gemini-embedding-2"),
        sa.Column("revision", sa.String(80), nullable=False, server_default="legacy-unverified"),
        sa.Column("dimensions", sa.Integer(), nullable=False, server_default="768"),
        sa.Column("input_format_version", sa.String(40), nullable=False, server_default="search-v1"),
    ):
        op.add_column("evidence_embeddings", column)
    op.create_check_constraint(
        "ck_evidence_vector_dims", "evidence_embeddings", "dimensions = 768 AND vector_dims(embedding) = dimensions"
    )
    op.create_check_constraint("ck_evidence_vector_norm", "evidence_embeddings", "vector_norm(embedding) > 0")
    op.create_table(
        "passage_embeddings",
        sa.Column("passage_id", sa.String(64), primary_key=True),
        sa.Column("profile", sa.String(200), primary_key=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id", "passage_id"], ["evidence_passages.source_id", "evidence_passages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "profile", "input_hash"],
            ["evidence_embeddings.source_id", "evidence_embeddings.profile", "evidence_embeddings.input_hash"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_passage_embeddings_source_id", "passage_embeddings", ["source_id"])
    op.create_table(
        "embedding_jobs",
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("passage_indexes.source_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("profile", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("run_token", sa.Uuid()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued','running','complete','failed','cancelled')", name="ck_embedding_job_status"
        ),
    )
    op.create_index("ix_embedding_jobs_pending", "embedding_jobs", ["status", "next_attempt_at"])


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM passage_embeddings) OR EXISTS (SELECT 1 FROM embedding_jobs) OR "
            "EXISTS (SELECT 1 FROM evidence_embeddings WHERE revision <> 'legacy-unverified' OR length(profile) > 100)"
        )
    ):
        raise RuntimeError("Cannot downgrade while vector memberships or jobs exist; remove derived indexes explicitly")
    op.drop_table("embedding_jobs")
    op.drop_table("passage_embeddings")
    op.drop_constraint("ck_evidence_vector_norm", "evidence_embeddings")
    op.drop_constraint("ck_evidence_vector_dims", "evidence_embeddings")
    for name in ("input_format_version", "dimensions", "revision", "model"):
        op.drop_column("evidence_embeddings", name)
    op.alter_column("evidence_embeddings", "profile", type_=sa.String(100), existing_type=sa.String(200))
    op.drop_constraint("uq_evidence_source_id", "evidence_passages")
    op.drop_column("passage_indexes", "vector_profile")
    op.drop_column("passage_indexes", "vector_fingerprint")
