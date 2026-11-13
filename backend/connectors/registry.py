"""Direct transports selected for this deployment; no provider logic crosses the envelope."""

from collections.abc import Callable
from functools import lru_cache

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization

from backend.connectors.config import ConnectorConfig
from backend.connectors.core import Connector, ConnectorError
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.slack import SlackConnector
from backend.connectors.vault import TokenVault
from backend.db.models import ConnectorAccount
from backend.schemas.connectors import Provider, Selection

ADAPTERS: dict[str, Callable[[ConnectorConfig, ConnectorAccount | None], Connector]] = {
    "gmail": GmailConnector,
    "google_drive": GoogleDriveConnector,
    "github": GitHubConnector,
    "slack": SlackConnector,
}


@lru_cache(maxsize=4)
def _pem_private_key_loads(pem: str) -> bool:
    try:
        serialization.load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return False
    return True


def github_signing_key_valid(config: ConnectorConfig) -> bool:
    """A truncated or unquoted multi-line PEM only fails later, inside GitHub App JWT signing."""
    key = config.github_private_key
    return key is not None and _pem_private_key_loads(key.get_secret_value())


def adapter(provider: Provider, config: ConnectorConfig, account: ConnectorAccount | None = None) -> Connector:
    if not configured(provider, config):
        raise ConnectorError(
            "not_configured",
            "Complete this provider's server setup: " + ", ".join(missing_configuration(provider, config)),
        )
    return ADAPTERS[provider](config, account)


def configured(provider: Provider, config: ConnectorConfig) -> bool:
    if not config.vault_key:
        return False
    try:
        TokenVault(config)
    except ConnectorError:
        return False
    if provider in {"gmail", "google_drive"}:
        return bool(config.google_client_id and config.google_client_secret)
    if provider == "github":
        return bool(
            config.github_client_id
            and config.github_client_secret
            and config.github_app_id
            and config.github_private_key
            and github_signing_key_valid(config)
        )
    return bool(config.slack_client_id and config.slack_client_secret and config.public_url.startswith("https://"))


def missing_configuration(provider: Provider, config: ConnectorConfig) -> list[str]:
    fields = ["vault_key"]
    fields += (
        ["google_client_id", "google_client_secret"] if provider in {"gmail", "google_drive"}
        else ["github_client_id", "github_client_secret", "github_app_id", "github_private_key"]
        if provider == "github" else ["slack_client_id", "slack_client_secret"]
    )
    missing = ["CONNECTORS__" + name.upper() for name in fields if not getattr(config, name)]
    if provider == "slack" and not config.public_url.startswith("https://"):
        missing.append("CONNECTORS__PUBLIC_URL (HTTPS required for Slack)")
    if provider == "github" and config.github_private_key and not github_signing_key_valid(config):
        missing.append("CONNECTORS__GITHUB_PRIVATE_KEY (invalid PEM private key; quote it and escape newlines as \\n)")
    if config.vault_key:
        try:
            TokenVault(config)
        except ConnectorError:
            missing.append("CONNECTORS__VAULT_KEY (invalid encryption key)")
    return missing


async def verify_selection(connector: Connector, selection: Selection) -> None:
    """Resolve browser selections with provider authorization before saving or renewing access."""
    if isinstance(connector, GmailConnector):
        allowed = {resource.id for resource in (await connector.list_resources()).resources}
        if not set(selection.resource_ids) <= allowed:
            raise ConnectorError("inaccessible", "A selected Gmail label is not authorized")
    elif isinstance(connector, GoogleDriveConnector):
        from backend.connectors.drive import drive_id  # noqa: PLC0415

        for identifier in selection.resource_ids:
            if not identifier.startswith(("drive:", "folder:", "file:")):
                raise ConnectorError("invalid_resource", "Choose a Drive file, folder or shared drive")
            validated = drive_id(identifier)
            if identifier.startswith("drive:"):
                await connector.get("/drives/" + validated)
            else:
                file = await connector.get(
                    "/files/" + validated, {"fields": "id,mimeType,trashed", "supportsAllDrives": "true"}
                )
                from backend.connectors.drive import FOLDER  # noqa: PLC0415

                if file.get("trashed") is True:
                    raise ConnectorError("not_found", "A selected Drive resource was removed")
                if (file.get("mimeType") == FOLDER) != identifier.startswith("folder:"):
                    raise ConnectorError("invalid_resource", "Drive resource type does not match the selection")
    elif isinstance(connector, GitHubConnector):
        for identifier in selection.resource_ids:
            repo = await connector.repository(identifier)
            await connector.installation_token(repo, selection)
            if identifier in selection.branches:
                from urllib.parse import quote  # noqa: PLC0415

                from backend.connectors.core import string  # noqa: PLC0415

                await connector.get(
                    f"/repos/{string(repo['full_name'])}/branches/{quote(selection.branches[identifier], safe='')}"
                )
    elif isinstance(connector, SlackConnector):
        for identifier in selection.resource_ids:
            await connector.channel(identifier)
    else:
        raise ConnectorError("not_configured", "Unknown connector adapter")
