"""Common asynchronous connector interface and strict helpers for external JSON."""

import base64
import hashlib
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID, uuid5

from backend.schemas.connectors import (
    AccessPolicy,
    Credentials,
    FetchResult,
    Provider,
    QueryResult,
    QuerySpec,
    RawSourceEnvelope,
    Resource,
    ResourcePage,
    Selection,
    SyncPage,
)


class ConnectorError(RuntimeError):
    def __init__(self, code: str, message: str, retry_after: float = 0) -> None:
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConnectorError("invalid_response", "Provider returned an invalid response")
    return cast(dict[str, object], value)


def records(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ConnectorError("invalid_response", "Provider returned an invalid collection")
    return [record(item) for item in value]


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ConnectorError("invalid_response", "Provider returned an invalid text field")
    return value


def strings(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ConnectorError("invalid_response", "Provider returned an invalid text collection")
    return [string(item) for item in value]


def number(value: object) -> int:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            return int(value)
        except ValueError:
            pass
    raise ConnectorError("invalid_response", "Provider returned an invalid numeric field")


def unbase64(value: object) -> bytes:
    encoded = string(value)
    try:
        return base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise ConnectorError("invalid_response", "Provider returned invalid encoded content") from error


def timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(string(value))
        if result.utcoffset() is None:
            raise ValueError("Missing time zone")
        return result
    except ValueError as error:
        raise ConnectorError("invalid_response", "Provider returned an invalid timestamp") from error


class Connector(Protocol):
    provider: Provider

    async def get_authorization_url(self, state: str, verifier: str) -> str: ...
    async def handle_callback(self, code: str, verifier: str) -> tuple[Credentials, str, str, dict[str, object]]: ...
    async def refresh(self, credentials: Credentials) -> Credentials: ...
    async def disconnect(self) -> None: ...
    async def list_resources(self, cursor: str | None = None) -> ResourcePage: ...
    async def initial_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage: ...
    async def incremental_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage: ...
    async def fetch_resource(self, resource: Resource, selection: Selection) -> FetchResult: ...
    async def handle_event(self, event: dict[str, object]) -> list[Resource]: ...
    async def structured_query(self, query: QuerySpec, selection: Selection) -> QueryResult: ...
    async def health_check(self) -> bool: ...


class EnvelopeFactory:
    def __init__(self, account_id: UUID, owner: str, provider: Provider, external_account_id: str) -> None:
        self.account_id, self.owner, self.provider, self.external_account_id = (
            account_id,
            owner,
            provider,
            external_account_id,
        )

    def text(
        self,
        resource: Resource,
        content: str,
        filename: str,
        mime: str,
        *,
        permissions: list[dict[str, object]] | None = None,
    ) -> RawSourceEnvelope:
        identifier = uuid5(self.account_id, resource.id)
        evidence = permissions or []
        access = AccessPolicy(
            application_owner=self.owner,
            provider_resource=resource.id,
            provider_permissions=evidence,
            owners=[
                str(p.get("emailAddress") or p["id"])
                for p in evidence
                if p.get("role") == "owner" and (p.get("emailAddress") or p.get("id"))
            ],
            users=[
                str(p.get("emailAddress") or p["id"])
                for p in evidence
                if p.get("type") == "user" and (p.get("emailAddress") or p.get("id"))
            ],
            groups=[
                str(p.get("emailAddress") or p["id"])
                for p in evidence
                if p.get("type") == "group" and (p.get("emailAddress") or p.get("id"))
            ],
            organizations=[str(p["domain"]) for p in evidence if p.get("type") == "domain" and p.get("domain")],
        )
        return RawSourceEnvelope(
            source_id=identifier,
            connector_account_id=self.account_id,
            provider=self.provider,
            external_id=resource.id,
            external_parent_id=resource.parent_id,
            external_version=resource.version or hashlib.sha256(content.encode()).hexdigest(),
            resource_type=resource.kind,
            filename=filename,
            mime_type=mime,
            content=content,
            metadata=resource.metadata,
            provenance={
                "external_account_id": self.external_account_id,
                "external_id": resource.id,
                "external_parent_id": resource.parent_id,
                "external_version": resource.version,
            },
            permissions=access,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            created_at_remote=timestamp(resource.metadata.get("created_at")),
            updated_at_remote=timestamp(resource.metadata.get("updated_at")),
        )
