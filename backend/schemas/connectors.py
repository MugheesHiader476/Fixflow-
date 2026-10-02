"""Provider-independent connector contracts; credentials never cross the source boundary."""

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator

from backend.schemas.pipeline import Contract

Provider = Literal["gmail", "google_drive", "github", "slack"]
ConnectionStatus = Literal[
    "connected", "syncing", "connected_with_warning", "reauth_required", "revoked", "error", "disconnected"
]
SyncStatus = Literal[
    "idle", "pending", "running", "waiting", "complete", "complete_with_warning", "failed", "cancelled"
]


class AccessPolicy(Contract):
    visibility: Literal["private"] = "private"
    application_owner: str
    provider_resource: str
    owners: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    provider_permissions: list[dict[str, object]] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RawSourceEnvelope(Contract):
    source_id: UUID
    connector_account_id: UUID
    provider: Provider
    external_id: str = Field(min_length=1, max_length=2048)
    external_parent_id: str | None = None
    external_version: str = Field(min_length=1, max_length=300)
    resource_type: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    content: str | None = None
    binary_asset_reference: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)
    permissions: AccessPolicy
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at_remote: datetime | None = None
    updated_at_remote: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def single_content(self) -> "RawSourceEnvelope":
        if (self.content is None) == (self.binary_asset_reference is None):
            raise ValueError("Provide exactly one source content or private asset reference")
        if self.content is not None and (not self.content.strip() or "\x00" in self.content):
            raise ValueError("Source content must contain usable text")
        return self

    def context(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content", "binary_asset_reference"})


class Credentials(Contract):
    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)


class Resource(Contract):
    id: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None
    version: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ResourcePage(Contract):
    resources: list[Resource]
    next_cursor: str | None = None


class Selection(Contract):
    resource_ids: list[str] = Field(default_factory=list, max_length=200)
    branches: dict[str, str] = Field(default_factory=dict, max_length=200)
    categories: list[Literal["code", "issues", "pull_requests", "commits", "releases"]] = Field(
        default=["code", "issues", "pull_requests", "commits", "releases"]
    )
    start_date: date | None = None
    end_date: date | None = None
    attachments: bool = False
    threads: bool = True
    files: bool = False
    mime_types: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def valid_selection(self) -> "Selection":
        if len(self.resource_ids) != len(set(self.resource_ids)):
            raise ValueError("Duplicate selections")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("End date must not precede start date")
        if any(not value or len(value) > 2048 or "\x00" in value for value in self.resource_ids):
            raise ValueError("Invalid resource selection")
        if any(not value or len(value) > 255 or "\x00" in value for value in self.branches.values()):
            raise ValueError("Invalid branch selection")
        if not set(self.branches) <= set(self.resource_ids):
            raise ValueError("Branches must belong to selected repositories")
        if any(not value or len(value) > 255 or "/" not in value or "\x00" in value for value in self.mime_types):
            raise ValueError("Invalid MIME type selection")
        return self


class SyncPage(Contract):
    resources: list[Resource] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    next_state: dict[str, object] = Field(default_factory=dict)
    checkpoint: dict[str, object] = Field(default_factory=dict)
    complete: bool = False
    reconcile: bool = False
    completed_inventory_groups: list[str] = Field(default_factory=list)


class FetchResult(Contract):
    envelopes: list[RawSourceEnvelope] = Field(default_factory=list)
    followups: list[Resource] = Field(default_factory=list)
    removed_ids: list[str] = Field(default_factory=list)
    completed_inventory_groups: list[str] = Field(default_factory=list)


class QuerySpec(Contract):
    operation: Literal["list", "count"] = "list"
    resource_id: str | None = Field(default=None, max_length=2048)
    sender: str | None = Field(default=None, max_length=254)
    recipient: str | None = Field(default=None, max_length=254)
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def valid_dates(self) -> "QuerySpec":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("End date must not precede start date")
        return self

    def selected_window(self, selection: Selection) -> Selection | None:
        starts = [value for value in (self.start_date, selection.start_date) if value is not None]
        ends = [value for value in (self.end_date, selection.end_date) if value is not None]
        start, end = max(starts) if starts else None, min(ends) if ends else None
        if start and end and start > end:
            return None
        return selection.model_copy(update={"start_date": start, "end_date": end})


class QueryResult(Contract):
    resources: list[Resource]
    count: int
    exact: bool
    next_cursor: str | None = None


class ConnectResponse(Contract):
    authorization_url: str


class DisconnectRequest(Contract):
    policy: Literal["retain", "soft_delete", "purge"] = "soft_delete"


class SyncRequest(Contract):
    mode: Literal["incremental", "reconcile"] = "incremental"


class AccountResponse(Contract):
    id: UUID
    provider: Provider
    display_name: str
    external_account_id: str
    status: ConnectionStatus
    authentication_status: str
    provider_health: str
    sync_status: SyncStatus
    last_successful_request: datetime | None
    last_sync_at: datetime | None
    error_message: str | None
    configuration: Selection
    progress: dict[str, int]
    created_at: datetime


class ProviderResponse(Contract):
    provider: Provider
    configured: bool
    setup_message: str | None
    accounts: list[AccountResponse]
    install_url: str | None = None
