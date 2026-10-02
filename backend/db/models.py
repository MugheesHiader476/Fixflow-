from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
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
    text,
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
        Index(
            "uq_source_owner_hash",
            "owner_id",
            "file_hash",
            unique=True,
            postgresql_where=text("external_identity IS NULL"),
        ),
        Index(
            "uq_source_external_identity",
            "owner_id",
            "external_identity",
            unique=True,
            postgresql_where=text("external_identity IS NOT NULL"),
        ),
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
    ingestion_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    external_identity: Mapped[str | None] = mapped_column(String(64))
    connector_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_accounts.id", name="fk_source_connector"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionArtifact(Timestamped, Base):
    __tablename__ = "ingestion_artifacts"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True)
    result: Mapped[dict[str, object]] = mapped_column(JSONB)


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


class ConnectorCredential(Owned, Timestamped, Base):
    __tablename__ = "connector_credentials"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    ciphertext: Mapped[str] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorAccount(Owned, Timestamped, Base):
    __tablename__ = "connector_accounts"
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", "external_account_id", name="uq_connector_account_identity"),
        CheckConstraint("provider IN ('gmail','google_drive','github','slack')", name="ck_connector_provider"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(30))
    transport_type: Mapped[str] = mapped_column(String(30), default="direct", server_default="direct")
    external_account_id: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="connected", server_default="connected")
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    credential_reference: Mapped[UUID | None] = mapped_column(ForeignKey("connector_credentials.id"))
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    meta: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    sync_cursor: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    sync_status: Mapped[str] = mapped_column(String(40), default="idle", server_default="idle")
    authentication_status: Mapped[str] = mapped_column(String(30), default="valid", server_default="valid")
    provider_health: Mapped[str] = mapped_column(String(30), default="unknown", server_default="unknown")
    last_successful_request: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_message: Mapped[str | None] = mapped_column(String(500))


class ConnectorOAuthState(Owned, Base):
    __tablename__ = "connector_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorSyncJob(Timestamped, Base):
    __tablename__ = "connector_sync_jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("connector_accounts.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), default="pending", server_default="pending", index=True)
    cursor: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    progress: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(500))


class ConnectorResource(Timestamped, Base):
    __tablename__ = "connector_resources"
    __table_args__ = (UniqueConstraint("account_id", "external_id", name="uq_connector_resource"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("connector_accounts.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(Text)
    external_parent_id: Mapped[str | None] = mapped_column(Text)
    external_version: Mapped[str] = mapped_column(String(300))
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="SET NULL"), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    permissions: Mapped[dict[str, object]] = mapped_column(JSONB)
    meta: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorSubscription(Base):
    __tablename__ = "connector_subscriptions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("connector_accounts.id", ondelete="CASCADE"), index=True)
    resource_id: Mapped[str] = mapped_column(String(500))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConnectorEvent(Base):
    __tablename__ = "connector_events"
    __table_args__ = (UniqueConstraint("provider", "external_event_id", name="uq_connector_event"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(30))
    external_event_id: Mapped[str] = mapped_column(String(300))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorAudit(Owned, Base):
    __tablename__ = "connector_audit"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("connector_accounts.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorRateLimit(Base):
    __tablename__ = "connector_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
