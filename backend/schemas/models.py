from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, Field, field_validator


def database_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Text cannot contain null bytes")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Text must contain valid Unicode characters") from error
    return value


PersistedText = Annotated[str, AfterValidator(database_text)]

SourceType = Literal["docs", "github", "community", "code"]
KnowledgeKind = Literal["docs", "github", "community", "upload"]
SourceStatus = Literal["uploaded", "processing", "chunked", "ready_for_embedding", "embedding", "indexed", "failed"]
IngestionFormat = Literal["document", "okf"]
EmbeddingStatus = Literal["not_configured", "pending", "processing", "complete", "failed"]
ShortText = Annotated[PersistedText, Field(max_length=200)]


class DebugAttachment(BaseModel):
    name: PersistedText = Field(min_length=1, max_length=255)
    content: PersistedText = Field(min_length=1, max_length=50_000)

    @field_validator("name", "content")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Attachments must contain nonblank text without null bytes")
        return value


class DebugRequest(BaseModel):
    question: PersistedText | None = Field(default=None, max_length=4000)
    error: PersistedText | None = Field(default=None, max_length=200_000)
    code: PersistedText | None = Field(default=None, max_length=500_000)
    context: PersistedText | None = Field(default=None, max_length=200_000)
    repo_url: PersistedText | None = Field(default=None, max_length=2_048)
    technology: ShortText | None = None
    techs: list[ShortText] = Field(default_factory=list, max_length=30)
    files: list[DebugAttachment] = Field(default_factory=list, max_length=5)

    @field_validator("question", "error", "code", "context")
    @classmethod
    def reject_null_bytes(cls, value: str | None) -> str | None:
        if value and "\x00" in value:
            raise ValueError("Diagnostic text cannot contain null bytes")
        return value

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, value: str | None) -> str | None:
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Repository URL must use HTTP or HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("Repository URL must not contain credentials")
        return value


class QuestionRequest(DebugRequest):
    question: PersistedText = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank")
        return value.strip()


class SourceDoc(BaseModel):
    id: str
    type: SourceType
    title: str
    publisher: str
    url: str
    relevance: int = Field(ge=0, le=100)
    excerpt: str
    used: bool = True
    source_id: str | None = None
    source_hash: str | None = None
    location: str | None = None


class FixStep(BaseModel):
    title: str
    detail: str


class CodeFix(BaseModel):
    file: str
    lines: str
    language: Literal["python", "typescript", "javascript", "bash", "sql"]
    before: str
    after: str


class AlternativeFix(BaseModel):
    title: str
    tradeoff: str
    summary: str


class RagChunk(BaseModel):
    doc: str
    score: float


class RagDetails(BaseModel):
    query: str
    expansions: list[str]
    retrieved: int = Field(ge=0)
    reranked: int = Field(ge=0)
    sourcesUsed: int = Field(ge=0)
    topChunks: list[RagChunk]
    retrievalMethod: Literal["keyword", "dense"] = "keyword"


class SourceReference(BaseModel):
    title: PersistedText = Field(max_length=500)
    type: SourceType
    id: str | None = None
    source_id: str | None = None
    url: str | None = None
    excerpt: PersistedText | None = Field(default=None, max_length=6000)
    location: str | None = None
    quote: PersistedText | None = Field(default=None, max_length=2000)
    number: int | None = Field(default=None, ge=1)


class AnswerCitation(SourceReference):
    id: str
    quote: PersistedText = Field(min_length=1, max_length=2000)
    number: int = Field(ge=1)


class GroundedAnswer(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    text: PersistedText = Field(min_length=1, max_length=20_000)
    citations: list[AnswerCitation] = Field(default_factory=list, max_length=30)
    model: str | None = None


class DiagnosisDraft(BaseModel):
    """Provider output, independent of storage and retrieval implementation."""

    status: Literal["likely-cause-found", "investigating", "no-cause"]
    confidence: int | None = Field(default=None, ge=0, le=100)
    detected: list[str]
    rootCause: str
    whyThisHappens: str
    recommendedFix: list[FixStep]
    codeFix: CodeFix | None = None
    alternatives: list[AlternativeFix]
    answer: GroundedAnswer | None = None


class Diagnosis(DiagnosisDraft):
    sessionId: str
    request: DebugRequest | None = None
    generation: Literal["disabled", "model", "legacy"] = "legacy"
    sources: list[SourceDoc]
    rag: RagDetails


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    question: PersistedText = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        question = value.strip()
        if not question or "\x00" in question:
            raise ValueError("Question cannot be blank or contain null bytes")
        return question


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "fixflow"]
    text: str
    sources: list[SourceReference] = Field(default_factory=list)
    answer: GroundedAnswer | None = None


class SessionSummary(BaseModel):
    id: str
    title: str
    technology: list[str]
    createdAt: datetime
    status: Literal["resolved", "unresolved", "in-progress"]
    confidence: int | None
    errorMessage: str


class KnowledgeSource(BaseModel):
    id: str
    source_id: str
    name: str
    kind: KnowledgeKind
    source_type: KnowledgeKind
    status: SourceStatus
    ingestion_format: IngestionFormat = "document"
    embedding_status: EmbeddingStatus = "not_configured"
    embedding_error: str | None = None
    chunks: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    documents: int = Field(ge=0)
    document_count: int = Field(ge=0)
    technology: str | None = None
    version: str | None = None
    url: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated: datetime
    detail: str
    is_active: bool = True
    retrieval_available: bool = True
    managed_by_connector: bool = False


class SourceAvailabilityRequest(BaseModel):
    active: bool = Field(strict=True)


class SavedSolution(BaseModel):
    id: str
    problem: str
    rootCause: str
    technology: list[str]
    fixSummary: str
    sources: list[SourceReference]
    savedAt: datetime


class SaveRequest(BaseModel):
    problem: PersistedText = Field(min_length=1, max_length=20_000)
    rootCause: PersistedText = Field(max_length=50_000)
    technology: list[ShortText] = Field(default_factory=list, max_length=30)
    fixSummary: PersistedText = Field(max_length=50_000)
    sources: list[SourceReference] = Field(default_factory=list, max_length=100)
