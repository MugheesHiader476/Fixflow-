"""Native protocol pagination, normalization and read-only transport tests without credentials."""

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from backend.config import get_settings
from backend.connectors.base import OAuthConnector
from backend.connectors.config import ConnectorConfig
from backend.connectors.core import ConnectorError
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.http import ProviderHttp
from backend.connectors.slack import SlackConnector
from backend.db.models import ConnectorAccount
from backend.schemas.connectors import QuerySpec, Resource, Selection
from backend.services.connector_sources import asset_path

pytestmark = pytest.mark.anyio


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ConnectorConfig:
    monkeypatch.setenv("FIXFLOW_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(OAuthConnector, "access_token", AsyncMock(return_value="test-access-token"))
    monkeypatch.setattr(OAuthConnector, "success", AsyncMock())
    monkeypatch.setattr("backend.connectors.slack.reserve", AsyncMock())
    return ConnectorConfig(google_client_id="client", google_client_secret=SecretStr("secret"), github_app_id="7")


def account(provider: str) -> ConnectorAccount:
    return ConnectorAccount(
        id=uuid4(),
        owner_id="user_test",
        provider=provider,
        external_account_id="T1",
        display_name="Account",
        configuration={"resource_ids": ["C1"]},
    )


def drive_file(identifier: str, mime: str = "text/plain", name: str = "guide.txt") -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "mimeType": mime,
        "version": "4",
        "parents": ["root"],
        "modifiedTime": "2026-10-01T00:00:00Z",
        "createdTime": "2026-09-01T00:00:00Z",
        "capabilities": {"canDownload": True},
        "permissions": [{"id": "p1", "type": "user", "role": "reader", "emailAddress": "person@example.test"}],
    }


async def test_drive_pagination_and_structure_preserving_export(isolated: ConnectorConfig) -> None:
    requests = []
    mime = "application/vnd.google-apps.document"
    native = drive_file("doc1", mime, "Technical guide")
    content = Path("backend/tests/fixtures/pipeline/guide.docx").read_bytes()

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/drives"):
            return httpx.Response(200, json={"drives": []})
        if path.endswith("/changes/startPageToken"):
            return httpx.Response(200, json={"startPageToken": "anchor"})
        if path.endswith("/files"):
            if request.url.params.get("pageToken") == "second":
                return httpx.Response(200, json={"files": [drive_file("doc2")]})
            return httpx.Response(200, json={"files": [native], "nextPageToken": "second"})
        if path.endswith("/files/doc1"):
            return httpx.Response(200, json=native)
        if path.endswith("/export"):
            assert request.url.params["mimeType"].endswith("wordprocessingml.document")
            return httpx.Response(200, content=content)
        raise AssertionError(path)

    connector = GoogleDriveConnector(
        isolated, account("google_drive"), ProviderHttp(isolated, httpx.MockTransport(respond))
    )
    shared = await connector.list_resources()
    first = await connector.list_resources(shared.next_cursor)
    second = await connector.list_resources(first.next_cursor)
    assert [r.id for r in first.resources + second.resources] == ["file:doc1", "file:doc2"]
    selection = Selection(resource_ids=["folder:root"])
    initial = await connector.initial_sync(selection, {})
    next_page = await connector.initial_sync(selection, initial.next_state)
    assert len(next_page.resources) == 1 and next_page.complete
    assert next_page.checkpoint["page_token"] == "anchor"
    result = await connector.fetch_resource(initial.resources[0], selection)
    envelope = result.envelopes[0]
    assert envelope.filename.endswith(".docx") and envelope.binary_asset_reference
    assert asset_path(envelope.binary_asset_reference).read_bytes() == content
    assert envelope.permissions.application_owner == "user_test"
    assert envelope.permissions.provider_permissions[0]["role"] == "reader"
    assert envelope.updated_at_remote and envelope.metadata["original_mime_type"] == mime
    assert all(request.method == "GET" for request in requests)


async def test_gmail_history_expiry_and_attachment_identity(isolated: ConnectorConfig) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/history"):
            return httpx.Response(404, json={"error": "expired"})
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "50"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
        if path.endswith("/messages/m1"):
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "historyId": "51",
                    "labelIds": ["INBOX"],
                    "internalDate": "1760000000000",
                    "payload": {
                        "headers": [{"name": "Subject", "value": "Guide"}],
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "headers": [{"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"}],
                                "body": {
                                    "data": base64.urlsafe_b64encode(
                                        "Résumé documentation and database operations.".encode("latin-1")
                                    ).decode()
                                },
                            },
                            {
                                "partId": "2",
                                "filename": "guide.txt",
                                "mimeType": "text/plain",
                                "body": {"attachmentId": "attachment1"},
                            },
                        ],
                    },
                },
            )
        if path.endswith("/attachments/attachment1"):
            return httpx.Response(
                200, json={"data": base64.urlsafe_b64encode(b"Independent attachment documentation.").decode()}
            )
        raise AssertionError(path)

    connector = GmailConnector(isolated, account("gmail"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    selection = Selection(resource_ids=["INBOX"], attachments=True)
    page = await connector.incremental_sync(selection, {"history_id": "expired"})
    assert page.reconcile and page.checkpoint["history_id"] == "50"
    fetched = await connector.fetch_resource(page.resources[0], selection)
    assert len(fetched.envelopes) == len(fetched.followups) == 1
    attachment = (await connector.fetch_resource(fetched.followups[0], selection)).envelopes[0]
    assert attachment.external_parent_id == "m1" and attachment.source_id != fetched.envelopes[0].source_id
    assert "data" not in attachment.metadata


async def test_github_selected_installation_token_files_comments_and_pages(
    isolated: ConnectorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = []
    config = isolated.model_copy(update={"page_size": 1})
    repo = {
        "id": 1,
        "full_name": "owner/repo",
        "default_branch": "main",
        "description": "Documentation",
        "private": True,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [{"id": 9, "app_id": 7}]})
        if path == "/user/installations/9/repositories":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "repositories": [repo] if request.url.params.get("page", "1") == "1" else [{**repo, "id": 2}],
                },
            )
        if path == "/repositories/1":
            return httpx.Response(200, json=repo)
        if path.endswith("/installation"):
            assert request.headers["Authorization"] == "Bearer app-jwt"
            return httpx.Response(200, json={"id": 9, "app_id": 7})
        if path == "/app/installations/9/access_tokens":
            data = json.loads(request.content)
            assert data["repository_ids"] == [1] and all(value == "read" for value in data["permissions"].values())
            return httpx.Response(201, json={"token": "installation-token-with-any-length"})
        if path.endswith("/git/blobs/blob1"):
            assert request.headers["Authorization"] == "Bearer installation-token-with-any-length"
            return httpx.Response(
                200, json={"content": base64.b64encode(b"def get_connection():\n    return 'postgresql'\n").decode()}
            )
        if path.endswith("/issues/1/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 99,
                        "body": "This comment explains the database connection issue.",
                        "user": {"id": 12},
                        "updated_at": "2026-10-01T00:00:00Z",
                    }
                ],
            )
        raise AssertionError(path)

    connector = GitHubConnector(config, account("github"), ProviderHttp(config, httpx.MockTransport(respond)))
    monkeypatch.setattr(connector, "app_jwt", lambda: "app-jwt")
    first = await connector.list_resources()
    second = await connector.list_resources(first.next_cursor)
    assert [r.id for r in first.resources + second.resources] == ["1", "2"]
    selection = Selection(resource_ids=["1"], categories=["code", "issues"])
    source = Resource(
        id="1:file:main:connection.py",
        name="connection.py",
        kind="source_file",
        version="blob1",
        metadata={
            "repository_id": "1",
            "file_path": "src/connection.py",
            "blob_sha": "blob1",
            "branch": "main",
            "commit_sha": "commit1",
        },
    )
    envelope = (await connector.fetch_resource(source, selection)).envelopes[0]
    assert envelope.filename == "connection.py" and envelope.metadata["branch"] == "main"
    page = connector.page_ref(
        Resource(id="1:issue:1", name="Issue", kind="issue", metadata={"repository_id": "1"}), "issue_comments", "1"
    )
    result = await connector.fetch_resource(page, selection)
    assert (
        result.envelopes[0].resource_type == "issue_comment" and result.envelopes[0].external_parent_id == "1:issue:1"
    )
    assert result.followups and result.followups[0].metadata["page"] == 2
    assert all("write" not in request.content.decode() for request in requests if request.method == "POST")


async def test_slack_user_oauth_pages_threads_authors_and_event_fetch(isolated: ConnectorConfig) -> None:
    requests = []
    root = {
        "type": "message",
        "ts": "1760000000.000001",
        "text": "Database troubleshooting guide.",
        "user": "U1",
        "reply_count": 1,
    }
    reply = {
        "type": "message",
        "ts": "1760000001.000002",
        "thread_ts": root["ts"],
        "text": "Check database pool capacity.",
        "user": "U1",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "conversations.info":
            return httpx.Response(
                200, json={"ok": True, "channel": {"id": "C1", "name": "private-docs", "is_private": True}}
            )
        if method == "conversations.history":
            assert int(request.url.params["limit"]) <= 15
            return httpx.Response(
                200, json={"ok": True, "messages": [root], "has_more": False, "response_metadata": {"next_cursor": ""}}
            )
        if method == "conversations.replies":
            return httpx.Response(200, json={"ok": True, "messages": [root, reply], "has_more": False})
        if method == "users.info":
            return httpx.Response(
                200, json={"ok": True, "user": {"id": "U1", "name": "documentation-author", "profile": {}}}
            )
        raise AssertionError(method)

    connector = SlackConnector(isolated, account("slack"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    assert "im:history" not in connector.scopes and not any("write" in scope for scope in connector.scopes)
    page = await connector.initial_sync(Selection(resource_ids=["C1"]), {})
    fetched = await connector.fetch_resource(page.resources[0], Selection(resource_ids=["C1"]))
    replies = await connector.fetch_resource(fetched.followups[0], Selection(resource_ids=["C1"]))
    assert len(replies.envelopes) == 2
    assert replies.envelopes[1].external_parent_id == "C1:" + str(root["ts"])
    assert replies.envelopes[1].metadata["author_name"] == "documentation-author"
    events = await connector.handle_event(
        {"event": {"type": "message", "channel": "C1", "subtype": "message_changed", "message": root}}
    )
    assert events and (await connector.fetch_resource(events[0], Selection(resource_ids=["C1"]))).envelopes
    with pytest.raises(ConnectorError):
        await connector.structured_query(QuerySpec(resource_id="C-other"), Selection(resource_ids=["C1"]))


async def test_github_incremental_run_refreshes_and_pins_the_branch_head(isolated: ConnectorConfig) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repositories/1":
            return httpx.Response(200, json={"id": 1, "full_name": "owner/repo", "default_branch": "main"})
        if request.url.path.endswith("/branches/main"):
            return httpx.Response(200, json={"commit": {"sha": "new-head"}})
        if request.url.path.endswith("/git/trees/new-head"):
            return httpx.Response(200, json={"tree": [{"type": "blob", "path": "current.py", "sha": "new-blob"}]})
        raise AssertionError(request.url.path)

    connector = GitHubConnector(isolated, account("github"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    selection = Selection(resource_ids=["1"], categories=["code"])
    page = await connector.incremental_sync(
        selection, {"stage": 1, "heads": {"1": "old-head"}, "since": "2026-10-01T00:00:00Z"}
    )
    assert page.complete and page.resources[0].metadata["commit_sha"] == "new-head"
    assert page.checkpoint["heads"] == {"1": "new-head"}
    assert page.completed_inventory_groups == ["repository:1:code:main"]
    unchanged = await connector.incremental_sync(selection, {**page.checkpoint, "stage": 1})
    assert unchanged.resources == [] and unchanged.completed_inventory_groups == []
    assert sum("/git/trees/" in request.url.path for request in requests) == 1


async def test_drive_shared_permissions_follow_every_page(isolated: ConnectorConfig) -> None:
    native = {**drive_file("shared1"), "driveId": "drive1"}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/permissions"):
            if request.url.params.get("pageToken") == "second":
                return httpx.Response(200, json={"permissions": [{"id": "p2", "type": "group", "role": "reader"}]})
            return httpx.Response(200, json={"permissions": native["permissions"], "nextPageToken": "second"})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"Shared drive technical documentation.")
        return httpx.Response(200, json=native)

    connector = GoogleDriveConnector(
        isolated, account("google_drive"), ProviderHttp(isolated, httpx.MockTransport(respond))
    )
    fetched = await connector.fetch_resource(
        Resource(id="file:shared1", name="guide.txt", kind="drive_file"), Selection(resource_ids=["drive:drive1"])
    )
    assert [item["id"] for item in fetched.envelopes[0].permissions.provider_permissions] == ["p1", "p2"]


async def test_gmail_structured_query_cannot_expand_selected_dates(isolated: ConnectorConfig) -> None:
    from datetime import date  # noqa: PLC0415

    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": []})

    connector = GmailConnector(isolated, account("gmail"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    selection = Selection(resource_ids=["INBOX"], start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
    result = await connector.structured_query(
        QuerySpec(resource_id="INBOX", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), limit=1), selection
    )
    assert result.exact and requests[0].url.params["maxResults"] == "1"
    assert requests[0].url.params["q"] == connector.date_query(selection)
    outside = await connector.structured_query(QuerySpec(resource_id="INBOX", start_date=date(2026, 10, 1)), selection)
    assert outside.exact and outside.count == 0 and len(requests) == 1


async def test_github_structured_query_paginates_only_selected_repositories(isolated: ConnectorConfig) -> None:
    connector = GitHubConnector(
        isolated,
        account("github"),
        ProviderHttp(
            isolated, httpx.MockTransport(lambda request: httpx.Response(200, json={"full_name": "owner/repo"}))
        ),
    )
    selection = Selection(resource_ids=["1", "2"])
    first = await connector.structured_query(QuerySpec(limit=1), selection)
    assert first.resources[0].id == "1" and not first.exact and first.next_cursor == "1"
    second = await connector.structured_query(QuerySpec(limit=1, cursor=first.next_cursor), selection)
    assert second.resources[0].id == "2" and second.exact and second.next_cursor is None
    with pytest.raises(ConnectorError):
        await connector.structured_query(QuerySpec(cursor="-1"), selection)


async def test_drive_structured_query_enforces_selection_and_page_bound(isolated: ConnectorConfig) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"drives": [{"id": "drive1", "name": "Selected shared drive"}]})

    connector = GoogleDriveConnector(
        isolated, account("google_drive"), ProviderHttp(isolated, httpx.MockTransport(respond))
    )
    selection = Selection(resource_ids=["drive:drive1"])
    result = await connector.structured_query(QuerySpec(limit=1), selection)
    assert result.resources[0].id == "drive:drive1" and requests[0].url.params["pageSize"] == "1"
    with pytest.raises(ConnectorError):
        await connector.structured_query(QuerySpec(resource_id="file:other"), selection)
