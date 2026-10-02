"""Owner isolation and explicit OKF/embedding job state, preserving existing rows."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

STATUS = "status IN ('uploaded','processing','chunked','ready_for_embedding','embedding','indexed','failed')"


def upgrade() -> None:
    for table in ("knowledge_sources", "debug_sessions", "saved_solutions"):
        op.add_column(table, sa.Column("owner_id", sa.String(200), nullable=False, server_default="__legacy__"))
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])
    op.drop_constraint("knowledge_sources_file_hash_key", "knowledge_sources", type_="unique")
    op.create_unique_constraint("uq_source_owner_hash", "knowledge_sources", ["owner_id", "file_hash"])
    op.add_column(
        "knowledge_sources", sa.Column("ingestion_format", sa.String(20), nullable=False, server_default="document")
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("embedding_status", sa.String(20), nullable=False, server_default="not_configured"),
    )
    op.add_column("knowledge_sources", sa.Column("embedding_error", sa.String(500)))
    op.drop_constraint("ck_source_status", "knowledge_sources", type_="check")
    op.create_check_constraint("ck_source_status", "knowledge_sources", STATUS)
    op.create_check_constraint("ck_ingestion_format", "knowledge_sources", "ingestion_format IN ('document','okf')")
    op.create_check_constraint(
        "ck_embedding_status",
        "knowledge_sources",
        "embedding_status IN ('not_configured','pending','processing','complete','failed')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM knowledge_sources GROUP BY file_hash HAVING count(*) > 1)")
    ):
        raise RuntimeError(
            "Cannot downgrade owner isolation while duplicate file hashes exist; preserve and migrate data first"
        )
    op.execute("UPDATE knowledge_sources SET status='ready_for_embedding' WHERE status='embedding'")
    for name in ("ck_embedding_status", "ck_ingestion_format", "ck_source_status"):
        op.drop_constraint(name, "knowledge_sources", type_="check")
    op.create_check_constraint("ck_source_status", "knowledge_sources", STATUS.replace("'embedding',", ""))
    for column in ("embedding_error", "embedding_status", "ingestion_format"):
        op.drop_column("knowledge_sources", column)
    op.drop_constraint("uq_source_owner_hash", "knowledge_sources", type_="unique")
    op.create_unique_constraint("knowledge_sources_file_hash_key", "knowledge_sources", ["file_hash"])
    for table in ("knowledge_sources", "debug_sessions", "saved_solutions"):
        op.drop_index(f"ix_{table}_owner_id", table_name=table)
        op.drop_column(table, "owner_id")
