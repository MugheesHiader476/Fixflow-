"""Direct transports selected for this deployment; no provider logic crosses the envelope."""

from collections.abc import Callable

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


def adapter(provider: Provider, config: ConnectorConfig, account: ConnectorAccount | None = None) -> Connector:
    if not configured(provider, config):
        raise ConnectorError("not_configured", "Configure this provider's server credentials and token vault first")
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
        )
    return bool(config.slack_client_id and config.slack_client_secret)


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
