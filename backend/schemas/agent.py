"""Owner-scoped bounded workspace workflows; model tool arguments grant no permissions."""

from datetime import datetime
from email.utils import parseaddr
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from backend.schemas.connectors import Provider
from backend.schemas.pipeline import Contract
from backend.security.policy import validate_text


def email_address(value: str) -> str:
    if any(character in value for character in "\r\n\x00") or len(value) > 254:
        raise ValueError("Invalid email address")
    name, address = parseaddr(value, strict=True)
    if name or address != value.strip() or address.count("@") != 1 or any(c.isspace() for c in address):
        raise ValueError("Use one exact email address")
    local, domain = address.rsplit("@", 1)
    if not local or "." not in domain or not address.isascii():
        raise ValueError("Invalid email address")
    return local + "@" + domain.lower()


class GrokConfig(Contract):
    api_key: SecretStr | None = None
    model: str | None = Field(default=None, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    reasoning_effort: Literal["low", "medium", "high"] | None = "low"
    timeout_seconds: float = Field(default=25, ge=1, le=60)
    max_steps: int = Field(default=12, ge=2, le=20)
    max_output_tokens: int = Field(default=1800, ge=300, le=4000)
    input_token_limit: int = Field(default=16000, ge=2000, le=32000)
    requests_per_minute: int = Field(default=10, ge=1, le=60)
    run_timeout_seconds: int = Field(default=600, ge=60, le=1800)

    @field_validator("api_key", "model", "reasoning_effort", mode="before")
    @classmethod
    def optional_value(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)


class RunRequest(Contract):
    question: str = Field(min_length=1, max_length=4000)
    project: str | None = Field(default=None, min_length=1, max_length=100)
    account_ids: list[UUID] = Field(default_factory=list, max_length=20)
    parent_run_id: UUID | None = None
    include_connections: bool = True
    action_mode: Literal["draft", "auto"] = "draft"
    allow_email_actions: bool = False
    allowed_recipients: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_request(self) -> "RunRequest":
        self.question = validate_text(self.question)
        if self.project is not None:
            self.project = validate_text(self.project, maximum=100)
        self.allowed_recipients = list(dict.fromkeys(email_address(value) for value in self.allowed_recipients))
        if self.action_mode == "auto" and not self.allowed_recipients:
            raise ValueError("Automatic sending requires exact authorized recipient addresses")
        return self

    @property
    def email_enabled(self) -> bool:
        return self.allow_email_actions or self.action_mode == "auto"


class ChatRequest(Contract):
    question: str = Field(min_length=1, max_length=4000)
    parent_message_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def valid_question(cls, value: str) -> str:
        return validate_text(value)


class ToolArgs(Contract):
    account_id: UUID


class SearchSnapshots(Contract):
    query: str = Field(min_length=1, max_length=500)
    provider: Provider | None = None


class SearchProvider(ToolArgs):
    query: str = Field(default="", max_length=300)
    resource_id: str | None = Field(default=None, max_length=2048)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=5, ge=1, le=10)


class ReadProvider(ToolArgs):
    resource_id: str = Field(min_length=1, max_length=2048)
    path: str | None = Field(default=None, max_length=1000)
    revision: str | None = Field(default=None, max_length=100)


class PrepareFile(ReadProvider):
    format: Literal["original", "pdf"] = "original"


class DraftEmail(ToolArgs):
    recipient: str = Field(max_length=254)
    subject: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n\x00]+$")
    body: str = Field(min_length=1, max_length=12000)
    reply_message_id: str | None = Field(default=None, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("recipient")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return email_address(value)


class Material(Contract):
    id: str
    title: str
    text: str = Field(max_length=12000)
    reference: dict[str, object]
    observed_at: datetime
    warning: str | None = None


class AgentCitation(Contract):
    evidence_id: str
    quote: str = Field(min_length=1, max_length=1200)


class AgentClaim(Contract):
    text: str = Field(min_length=1, max_length=2000)
    citations: list[AgentCitation] = Field(min_length=1, max_length=8)


class Finish(Contract):
    answer: str = Field(min_length=1, max_length=12000)
    claims: list[AgentClaim] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class ActionApproval(Contract):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ActionView(Contract):
    id: UUID
    account_id: UUID
    status: str
    payload: dict[str, object]
    payload_hash: str
    provider_message_id: str | None
    error: str | None


class FileView(Contract):
    id: UUID
    filename: str
    mime_type: str
    download_path: str


class RunView(Contract):
    id: UUID
    question: str
    status: str
    answer: Finish | None
    materials: list[Material]
    actions: list[ActionView]
    files: list[FileView]
    steps: int
    usage: dict[str, int | None]
    error: str | None
    created_at: datetime
    parent_run_id: UUID | None = None
