"""Shared OAuth, refresh, safe downloads and read-only provider requests."""

import base64
import hashlib
import io
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import UploadFile
from pydantic import SecretStr
from sqlalchemy import select

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError, EnvelopeFactory, number, record, string
from backend.connectors.http import ProviderHttp
from backend.connectors.vault import TokenVault
from backend.db.models import ConnectorAccount, ConnectorAudit
from backend.db.session import get_session_factory
from backend.schemas.connectors import Credentials, Provider, RawSourceEnvelope, Resource
from backend.services.uploads import discard_upload, safe_filename, save_upload


class OAuthConnector:
    provider: Provider
    scopes: tuple[str, ...] = ()
    authorization_endpoint = ""
    exchange_endpoint = ""
    api_root = ""

    def __init__(
        self, config: ConnectorConfig, account: ConnectorAccount | None = None, http: ProviderHttp | None = None
    ) -> None:
        self.config, self.account = config, account
        self.http = http or ProviderHttp(config)

    @property
    def client_id(self) -> str:
        value = (
            self.config.google_client_id
            if self.provider in {"gmail", "google_drive"}
            else getattr(self.config, self.provider + "_client_id")
        )
        if not isinstance(value, str) or not value:
            raise ConnectorError("not_configured", "Provider OAuth client is not configured")
        return value

    @property
    def client_secret(self) -> str:
        value = (
            self.config.google_client_secret
            if self.provider in {"gmail", "google_drive"}
            else getattr(self.config, self.provider + "_client_secret")
        )
        if not isinstance(value, SecretStr):
            raise ConnectorError("not_configured", "Provider OAuth secret is not configured")
        return value.get_secret_value()

    @property
    def callback_url(self) -> str:
        return self.config.public_url + f"/api/connectors/{self.provider}/callback"

    @property
    def factory(self) -> EnvelopeFactory:
        if self.account is None:
            raise ConnectorError("not_connected", "Connect a provider account first")
        return EnvelopeFactory(self.account.id, self.account.owner_id, self.provider, self.account.external_account_id)

    async def get_authorization_url(self, state: str, verifier: str) -> str:
        parameters = {
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "state": state,
            "response_type": "code",
        }
        if self.provider == "slack":
            parameters["user_scope"] = ",".join(self.scopes)
        else:
            parameters.update(
                code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("="),
                code_challenge_method="S256",
            )
            if self.provider in {"gmail", "google_drive"}:
                parameters.update(scope=" ".join(self.scopes), access_type="offline", prompt="consent")
        return self.authorization_endpoint + "?" + urlencode(parameters)

    def token_result(self, result: dict[str, object], previous: Credentials | None = None) -> Credentials:
        if result.get("error") or result.get("ok") is False:
            raise ConnectorError(
                "reauth_required", "Provider authorization failed; reconnect and grant the required read permissions"
            )
        payload = record(result["authed_user"]) if self.provider == "slack" and "authed_user" in result else result
        scopes = string(payload.get("scope", "")).replace(",", " ").split()
        if not scopes and previous:
            scopes = previous.scopes
        if any(
            scope not in scopes for scope in self.scopes if scope.startswith("https://") and "userinfo" not in scope
        ):
            raise ConnectorError("missing_scope", "Required read permissions were not granted")
        if self.provider == "slack" and not set(self.scopes) <= set(scopes):
            raise ConnectorError("missing_scope", "Required Slack read permissions were not granted")
        refresh = payload.get("refresh_token")
        expires = payload.get("expires_in")
        return Credentials(
            access_token=SecretStr(string(payload["access_token"])),
            refresh_token=SecretStr(string(refresh)) if refresh else (previous.refresh_token if previous else None),
            expires_at=datetime.now(UTC) + timedelta(seconds=number(expires)) if expires else None,
            scopes=scopes,
        )

    async def exchange(self, code: str, verifier: str) -> Credentials:
        values = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.callback_url,
        }
        if self.provider != "slack":
            values.update(grant_type="authorization_code", code_verifier=verifier)
        return self.token_result(record(await self.http.request("POST", self.exchange_endpoint, data=values)))

    async def refresh(self, credentials: Credentials) -> Credentials:
        if credentials.refresh_token is None:
            raise ConnectorError("reauth_required", "Authorization expired; reconnect this account")
        result = record(
            await self.http.request(
                "POST",
                self.exchange_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": credentials.refresh_token.get_secret_value(),
                },
            )
        )
        return self.token_result(result, credentials)

    async def access_token(self) -> str:
        if self.account is None:
            raise ConnectorError("not_connected", "Connect a provider account first")
        vault = TokenVault(self.config)
        async with get_session_factory().begin() as db:
            account = await db.scalar(
                select(ConnectorAccount).where(ConnectorAccount.id == self.account.id).with_for_update()
            )
            if (
                account is None
                or account.status not in {"connected", "syncing", "connected_with_warning"}
                or account.credential_reference is None
            ):
                raise ConnectorError("reauth_required", "Connection is not authorized; reconnect this account")
            credentials = await vault.retrieve(db, account.owner_id, account.credential_reference, lock=True)
            if credentials.expires_at and credentials.expires_at <= datetime.now(UTC) + timedelta(seconds=60):
                credentials = await self.refresh(credentials)
                await vault.rotate(db, account.owner_id, account.credential_reference, credentials)
                db.add(
                    ConnectorAudit(
                        owner_id=account.owner_id, account_id=account.id, event="token_refreshed", details={}
                    )
                )
            return credentials.access_token.get_secret_value()

    async def get(
        self, path: str, params: dict[str, str | int] | None = None, *, token: str | None = None
    ) -> dict[str, object]:
        result = record(
            await self.http.request(
                "GET",
                self.api_root + path,
                token=token or await self.access_token(),
                params=params,
                headers=self.api_headers(),
            )
        )
        await self.success()
        return result

    def api_headers(self) -> dict[str, str]:
        return {}

    async def success(self) -> None:
        if self.account:
            async with get_session_factory().begin() as db:
                account = await db.get(ConnectorAccount, self.account.id)
                if account:
                    account.last_successful_request, account.provider_health = datetime.now(UTC), "available"

    async def download(
        self, path: str, params: dict[str, str | int] | None = None, *, token: str | None = None
    ) -> bytes:
        result = await self.http.request(
            "GET",
            self.api_root + path,
            token=token or await self.access_token(),
            params=params,
            headers=self.api_headers(),
            raw=True,
        )
        if not isinstance(result, bytes):
            raise ConnectorError("invalid_response", "Provider download returned invalid content")
        await self.success()
        return result

    async def binary(
        self,
        resource: Resource,
        data: bytes,
        filename: str,
        mime: str,
        *,
        permissions: list[dict[str, object]] | None = None,
    ) -> RawSourceEnvelope:
        from backend.config import get_settings  # noqa: PLC0415

        name = safe_filename(filename)
        path, digest = await save_upload(name, UploadFile(io.BytesIO(data), filename=name), None)
        try:
            relative = path.relative_to(get_settings().upload_dir)
            envelope = self.factory.text(resource, "Private binary asset", name, mime, permissions=permissions)
            return envelope.model_copy(
                update={"content": None, "binary_asset_reference": "upload:" + str(relative), "content_hash": digest}
            )
        except (ConnectorError, ValueError):
            discard_upload(path)
            raise

    async def disconnect(self) -> None:
        token = await self.access_token()
        await self.revoke_token(token)

    async def revoke_token(self, token: str) -> None:
        if self.provider in {"gmail", "google_drive"}:
            await self.http.request("POST", "https://oauth2.googleapis.com/revoke", data={"token": token}, raw=True)
        elif self.provider == "slack":
            result = record(await self.http.request("POST", "https://slack.com/api/auth.revoke", token=token))
            if result.get("ok") is not True:
                raise ConnectorError("provider_error", "Remote revocation failed; local access has been disabled")
        elif self.provider == "github":
            await self.http.request(
                "DELETE",
                f"https://api.github.com/applications/{self.client_id}/token",
                basic=(self.client_id, self.client_secret),
                payload={"access_token": token},
            )
