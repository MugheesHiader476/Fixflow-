"""Separate, opt-in passage/answer contracts; canonical parsing remains contract 3."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from backend.processing.embedding_input import DIMENSIONS, MODEL
from backend.schemas.models import PersistedText
from backend.schemas.pipeline import Contract, Provenance


class EvidenceConfig(Contract):
    api_key: SecretStr | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: float = Field(default=12, ge=1, le=20)
    max_output_tokens: int = Field(default=800, ge=200, le=1200)
    requests_per_minute: int = Field(default=5, ge=1, le=30)

    @field_validator("api_key", "model", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> object:
        return None if value == "" else value


class GeminiConfig(Contract):
    """Explicit remote semantic search; fixed model/space, no automatic fallback model."""

    api_key: SecretStr | None = None
    model: Literal["gemini-embedding-2"] | None = None
    revision: str = Field(default=MODEL, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")
    dimensions: int = Field(default=DIMENSIONS, ge=DIMENSIONS, le=DIMENSIONS)
    timeout_seconds: float = Field(default=12, ge=1, le=15)
    requests_per_minute: int = Field(default=5, ge=1, le=30)
    min_similarity: float = Field(default=0.5, ge=0, le=1)

    @field_validator("api_key", "model", mode="before")
    @classmethod
    def optional_key(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)


class EmbeddingReceipt(Contract):
    source_id: str
    prepared: int = Field(ge=0)
    remaining: int = Field(ge=0)
    embedding_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    elapsed_ms: float = Field(default=0, ge=0)
    status: Literal["not_queued", "queued", "running", "complete", "failed", "cancelled"] = "not_queued"
    active: bool = False
    error: str | None = None


class IndexRequest(Contract):
    project: PersistedText = Field(min_length=1, max_length=100, pattern=r"^[^\r\n]+$")
    title: PersistedText | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[^\r\n]+$")
    document_date: date | None = None
    force: bool = False

    @model_validator(mode="after")
    def nonblank(self) -> "IndexRequest":
        self.project = self.project.strip()
        if not self.project or (self.title is not None and not self.title.strip()):
            raise ValueError("Project and title must be nonblank")
        return self


class EvidenceRequest(Contract):
    question: PersistedText = Field(min_length=1, max_length=2000)
    project: PersistedText = Field(min_length=1, max_length=100)
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    expand_context: bool = True
    synthesize: bool = False
    retrieval: Literal["lexical", "hybrid"] = "lexical"
    max_passages: int = Field(default=6, ge=1, le=6)
    context_tokens: int = Field(default=3000, ge=1, le=3000)
    candidate_limit: int = Field(default=24, ge=1, le=100)
    all_projects: bool = False
    connector_account_ids: list[UUID] | None = Field(default=None, max_length=20)
    include_manual_sources: bool = True

    @model_validator(mode="after")
    def nonblank(self) -> "EvidenceRequest":
        self.question, self.project = self.question.strip(), self.project.strip()
        if not self.question or not self.project:
            raise ValueError("Question and project must be nonblank")
        return self


class Span(Contract):
    block_id: str
    start: int = Field(default=0, ge=0)
    end: int = Field(ge=1)
    # Table offsets refer to canonical rows, all other offsets to Unicode characters.
    unit: Literal["characters", "rows"] = "characters"
    provenance: Provenance


class Passage(Contract):
    id: str
    source_id: str
    source_hash: str
    canonical_hash: str
    ordinal: int = Field(ge=0)
    kind: Literal["prose", "email", "slack", "code", "table"]
    group: str
    prefix: str
    content: str = Field(min_length=1)
    tokens: int = Field(ge=1, le=650)
    spans: list[Span] = Field(min_length=1)
    metadata: dict[str, object]
    warnings: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return self.prefix + "\n\n" + self.content


class EvidenceHit(Contract):
    passage: Passage
    score: float
    reason: Literal["match", "neighbour", "thread_root", "thread_reply"] = "match"
    lexical_rank: int | None = None
    vector_rank: int | None = None
    similarity: float | None = None
    embedding_profile: str | None = None


class EvidenceResult(Contract):
    passages: list[EvidenceHit]
    coverage: str
    warnings: list[str]
    elapsed_ms: float
    evidence_tokens: int
    model_calls: int = 0
    embedding_calls: int = 0
    embedding_input_tokens: int | None = None
    retrieval: Literal["lexical", "hybrid"] = "lexical"


class ClaimCitation(Contract):
    passage_id: str
    quote: str = Field(min_length=1, max_length=1200)


class AnswerClaim(Contract):
    text: str = Field(min_length=1, max_length=1200)
    citations: list[ClaimCitation] = Field(min_length=1, max_length=6)


class AnswerDraft(Contract):
    status: Literal["answered", "insufficient_evidence"]
    claims: list[AnswerClaim] = Field(max_length=6)
    limitations: list[str] = Field(max_length=6)


class EvidenceAnswer(Contract):
    status: Literal["answered", "evidence_only", "insufficient_evidence", "tool_required", "generation_unavailable"]
    text: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    evidence: EvidenceResult
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    elapsed_ms: float = 0
