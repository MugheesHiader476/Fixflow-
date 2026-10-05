"""Index persisted chunk context while preserving raw content and legacy chunks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def replace_search_projection(expression: str) -> None:
    # This column is derived. Source/chunk identities, content, metadata and vectors are untouched.
    op.drop_index("ix_chunk_search", table_name="document_chunks")
    op.drop_column("document_chunks", "search_text")
    op.add_column(
        "document_chunks",
        sa.Column("search_text", postgresql.TSVECTOR(), sa.Computed(expression, persisted=True), nullable=False),
    )
    op.create_index("ix_chunk_search", "document_chunks", ["search_text"], postgresql_using="gin")


def upgrade() -> None:
    replace_search_projection(
        "to_tsvector('simple', CASE WHEN right(metadata->>'retrieval_content', length(content)) = content "
        "THEN metadata->>'retrieval_content' ELSE content END)"
    )


def downgrade() -> None:
    replace_search_projection("to_tsvector('simple', content)")
