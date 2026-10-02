from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Owned:
    owner_id: Mapped[str] = mapped_column(String(200), default="__legacy__", server_default="__legacy__", index=True)


class KnowledgeSource(Owned, Timestamped, Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("owner_id", "file_hash", name="uq_source_owner_hash"),
        CheckConstraint("ingestion_format IN ('document','okf')", name="ck_ingestion_format"),
        CheckConstraint(
            "embedding_status IN ('not_configured','pending','processing','complete','failed')",
            name="ck_embedding_status",
        ),
        CheckConstraint(
            "status IN ('uploaded','processing','chunked','ready_for_embedding','embedding','indexed','failed')",
            name="ck_source_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(30))
    technology: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(200))
    path: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="uploaded", index=True)
    error_message: Mapped[str | None] = mapped_column(String(500))
    ingestion_format: Mapped[str] = mapped_column(String(20), default="document", server_default="document")
    embedding_status: Mapped[str] = mapped_column(String(20), default="not_configured", server_default="not_configured")
    embedding_error: Mapped[str | None] = mapped_column(String(500))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_document_source_content"),
        UniqueConstraint("id", "source_id", name="uq_document_id_source"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True)
    document_index: Mapped[int] = mapped_column(Integer)
    page_content: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentChunk(Timestamped, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "source_id"], ["documents.id", "documents.source_id"], ondelete="CASCADE"),
        UniqueConstraint("source_id", "content_hash", name="uq_chunk_source_content"),
        Index("ix_chunk_search", "search_text", postgresql_using="gin"),
        CheckConstraint(
            "(embedding IS NULL AND embedding_model IS NULL AND embedding_dimension IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dimension IS NOT NULL "
            "AND embedding_dimension > 0 AND vector_dims(embedding) = embedding_dimension)",
            name="ck_embedding_metadata",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(128), unique=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, default=dict)
    # Dimension is intentionally unconstrained until the real embedding model is selected.
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    search_text: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True))


class DebugSession(Owned, Timestamped, Base):
    __tablename__ = "debug_sessions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    diagnosis: Mapped[dict[str, object]] = mapped_column(JSONB)


class ChatEntry(Base):
    __tablename__ = "chat_messages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("debug_sessions.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedSolution(Owned, Timestamped, Base):
    __tablename__ = "saved_solutions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
