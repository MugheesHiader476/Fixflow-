"""Add a parsing terminal state without modifying or deleting existing records."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

LEGACY = "status IN ('uploaded','processing','chunked','ready_for_embedding','embedding','indexed','failed')"


def upgrade() -> None:
    op.drop_constraint("ck_source_status", "knowledge_sources", type_="check")
    op.create_check_constraint(
        "ck_source_status", "knowledge_sources", LEGACY.replace("('uploaded'", "('parsed','uploaded'")
    )


def downgrade() -> None:
    # Downgrade must never relabel a parsed source as chunked/indexed or silently erase data.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM knowledge_sources WHERE status='parsed')")):
        raise RuntimeError("Cannot downgrade while parsed sources exist; preserve and explicitly reprocess them first")
    op.drop_constraint("ck_source_status", "knowledge_sources", type_="check")
    op.create_check_constraint("ck_source_status", "knowledge_sources", LEGACY)
