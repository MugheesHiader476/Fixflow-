"""Native protocol pagination, normalization and read-only transport tests without credentials."""

import base64
import json
from datetime import date
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
        if path.endswith("/files/root"):
            return httpx.Response(200, json=drive_file("root", "application/vnd.google-apps.folder", "root"))
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


async def test_gmail_external_body_and_folded_header(isolated: ConnectorConfig) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if "/attachments/" in request.url.path:
            return httpx.Response(200, json={"data": base64.urlsafe_b64encode(b"Large body documentation.").decode()})
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "historyId": "10",
                "labelIds": ["INBOX"],
                "internalDate": "1760000000000",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [{"name": "Subject", "value": "Folded\r\n header"}],
                    "body": {"attachmentId": "body1"},
                },
            },
        )

    connector = GmailConnector(isolated, account("gmail"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    result = await connector.fetch_resource(
        Resource(id="m1", name="m1", kind="email_message"), Selection(resource_ids=["INBOX"])
    )
    assert "Large body documentation." in str(result.envelopes[0].content)
    assert "subject: folded" in str(result.envelopes[0].content).casefold()
    assert not result.followups


async def test_gmail_attachment_rechecks_parent_selection(isolated: ConnectorConfig) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"internalDate": "1760000000000", "labelIds": ["UNSELECTED"]})

    connector = GmailConnector(isolated, account("gmail"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    result = await connector.fetch_resource(
        Resource(
            id="m1:attachment:1",
            name="guide.txt",
            kind="email_attachment",
            metadata={"message_id": "m1"},
        ),
        Selection(resource_ids=["INBOX"], attachments=True),
    )
    assert result.removed_ids == ["m1", "m1:attachment:1"]
    assert len(calls) == 1 and calls[0].url.params["format"] == "metadata"


async def test_drive_shared_feeds_keep_independent_cursors(isolated: ConnectorConfig) -> None:
    calls = []
    native = {**drive_file("shared1"), "driveId": "d1"}

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/startPageToken"):
            return httpx.Response(
                200, json={"startPageToken": "drive-anchor" if request.url.params.get("driveId") else "user-anchor"}
            )
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [native]})
        if request.url.path.endswith("/changes"):
            shared = request.url.params.get("driveId") == "d1"
            assert request.url.params["pageToken"] == ("drive-anchor" if shared else "user-anchor")
            return httpx.Response(
                200,
                json={
                    "changes": [{"fileId": "shared1", "file": native}] if shared else [],
                    "newStartPageToken": "drive-next" if shared else "user-next",
                },
            )
        raise AssertionError(request.url.path)

    connector = GoogleDriveConnector(
        isolated, account("google_drive"), ProviderHttp(isolated, httpx.MockTransport(respond))
    )
    selection = Selection(resource_ids=["drive:d1"])
    initial = await connector.initial_sync(selection, {})
    assert initial.complete and initial.checkpoint["drive_tokens"] == {"d1": "drive-anchor"}
    first = await connector.incremental_sync(selection, initial.checkpoint)
    assert not first.complete and first.checkpoint["page_token"] == "user-next"
    second = await connector.incremental_sync(selection, first.next_state)
    assert second.complete and second.resources[0].id == "file:shared1"
    assert second.checkpoint["page_token"] == "user-next"
    assert second.checkpoint["drive_tokens"] == {"d1": "drive-next"}
    assert sum(request.url.path.endswith("/changes") for request in calls) == 2


async def test_github_truncated_tree_walk_resumes_subtrees(isolated: ConnectorConfig) -> None:
    calls = []
    config = isolated.model_copy(update={"page_size": 1})

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/repositories/1":
            return httpx.Response(200, json={"id": 1, "full_name": "owner/repo", "default_branch": "main"})
        if path.endswith("/branches/main"):
            return httpx.Response(200, json={"commit": {"sha": "head"}})
        if path.endswith("/git/trees/head"):
            if request.url.params.get("recursive"):
                return httpx.Response(
                    200,
                    json={"truncated": True, "tree": [{"type": "blob", "path": "incomplete.txt", "sha": "discard"}]},
                )
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"type": "blob", "path": "README.md", "sha": "b1"},
                        {"type": "tree", "path": "src", "sha": "subtree"},
                    ]
                },
            )
        if path.endswith("/git/trees/subtree"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"type": "blob", "path": "a.py", "sha": "b2"},
                        {"type": "blob", "path": "b.py", "sha": "b3"},
                    ]
                },
            )
        raise AssertionError(path)

    connector = GitHubConnector(config, account("github"), ProviderHttp(config, httpx.MockTransport(respond)))
    state: dict[str, object] = {"stage": 1}
    refs = []
    for _ in range(8):
        page = await connector.initial_sync(Selection(resource_ids=["1"], categories=["code"]), state)
        refs.extend(page.resources)
        state = json.loads(json.dumps(page.next_state))  # Reload the persisted checkpoint after each step.
        if page.complete:
            break
    assert page.complete and [ref.name for ref in refs] == ["README.md", "src/a.py", "src/b.py"]
    assert page.completed_inventory_groups == ["repository:1:code:main"]
    assert sum("/git/trees/" in request.url.path for request in calls) == 3


async def test_github_empty_repository_still_syncs_metadata_and_issues(isolated: ConnectorConfig) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repositories/1":
            return httpx.Response(200, json={"id": 1, "full_name": "owner/empty", "default_branch": "main"})
        if request.url.path.endswith("/branches/main"):
            return httpx.Response(404)
        if request.url.path.endswith(("/branches", "/issues", "/releases")):
            return httpx.Response(200, json=[])
        raise AssertionError(request.url.path)

    connector = GitHubConnector(isolated, account("github"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    state: dict[str, object] = {}
    refs = []
    for _ in range(8):
        page = await connector.initial_sync(
            Selection(resource_ids=["1"], categories=["code", "issues", "commits", "releases"]), state
        )
        refs.extend(page.resources)
        state = page.next_state
        if page.complete:
            break
    assert page.complete and [ref.kind for ref in refs] == ["repository_metadata"]
    assert page.checkpoint["heads"] == {"1": ""}


async def test_slack_thread_and_events_respect_dates(isolated: ConnectorConfig) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/conversations.info"):
            return httpx.Response(200, json={"ok": True, "channel": {"id": "C1", "name": "docs", "is_private": False}})
        if request.url.path.endswith("/conversations.replies"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [
                        {"ts": "1760000000.000001", "text": "Old root."},
                        {"ts": "1760100000.000001", "text": "Selected reply.", "thread_ts": "1760000000.000001"},
                    ],
                },
            )
        raise AssertionError(request.url.path)

    connector = SlackConnector(isolated, account("slack"), ProviderHttp(isolated, httpx.MockTransport(respond)))
    selection = Selection(resource_ids=["C1"], start_date=date(2025, 10, 10), end_date=date(2025, 10, 10))
    result = await connector.fetch_resource(
        Resource(
            id="C1:thread:old",
            name="Thread",
            kind="slack_thread_page",
            metadata={"channel_id": "C1", "thread_ts": "1760000000.000001"},
        ),
        selection,
    )
    assert len(result.envelopes) == 1
    assert result.removed_ids == ["C1:1760000000.000001"]
    assert result.completed_inventory_groups == ["slack_thread:C1:1760000000.000001"]
    event = await connector.fetch_resource(
        Resource(
            id="C1:event:old",
            name="Changed",
            kind="slack_event_message",
            metadata={"channel_id": "C1", "message_ts": "1760000000.000001"},
        ),
        selection,
    )
    assert event.removed_ids and not event.envelopes


async def test_private_file_redirects_stay_inside_provider(isolated: ConnectorConfig) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer private-test-token"
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://files.slack.com/download"})
        if request.url.path == "/unsafe":
            return httpx.Response(302, headers={"Location": "https://api.github.com/receive"})
        return httpx.Response(200, content=b"Private document")

    http = ProviderHttp(isolated, httpx.MockTransport(respond))
    assert (
        await http.request("GET", "https://slack.com/start", token="private-test-token", raw=True)
        == b"Private document"
    )
    with pytest.raises(ConnectorError, match="redirect is not authorized"):
        await http.request("GET", "https://files.slack.com/unsafe", token="private-test-token", raw=True)
    assert not any(request.url.host == "api.github.com" for request in calls)


async def test_github_commit_files_paginate_and_category_selection_applies(isolated: ConnectorConfig) -> None:
    config = isolated.model_copy(update={"page_size": 1})

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repositories/1":
            return httpx.Response(200, json={"id": 1, "full_name": "owner/repo"})
        if request.url.path.endswith("/commits/abc123"):
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={
                    "files": []
                    if page == 3
                    else [
                        {
                            "filename": f"file{page}.py",
                            "sha": f"blob{page}",
                            "status": "modified",
                            "patch": "+return postgres",
                        }
                    ]
                },
            )
        raise AssertionError(request.url.path)

    connection = account("github")
    connection.configuration = {"resource_ids": ["1"], "categories": ["code"]}
    connector = GitHubConnector(config, connection, ProviderHttp(config, httpx.MockTransport(respond)))
    parent = Resource(
        id="1:commit:abc123",
        name="Commit",
        kind="commit",
        metadata={
            "repository_id": "1",
            "number": "abc123",
            "object": {"commit": {"message": "Fix pool handling"}},
        },
    )
    fetched = await connector.fetch_resource(parent, Selection(resource_ids=["1"], categories=["commits"]))
    ref = fetched.followups[0]
    envelopes = []
    for _ in range(4):
        page = await connector.fetch_resource(ref, Selection(resource_ids=["1"], categories=["commits"]))
        envelopes.extend(page.envelopes)
        if not page.followups:
            break
        ref = page.followups[0]
    assert len(envelopes) == 2 and all(envelope.external_parent_id == parent.id for envelope in envelopes)
    assert page.completed_inventory_groups == ["relationships:1:commit:abc123:commit_files"]
    assert all("+return postgres" in str(envelope.content) for envelope in envelopes)
    assert not await connector.handle_event({"repository": {"id": 1}, "issue": {"number": 1}})


@pytest.mark.parametrize("cls", [GoogleDriveConnector, GitHubConnector])
async def test_discovery_rejects_malformed_cursors(
    isolated: ConnectorConfig, cls: type[GoogleDriveConnector] | type[GitHubConnector]
) -> None:
    connector = cls(isolated)
    with pytest.raises(ConnectorError, match="cursor is invalid"):
        await connector.list_resources("{malformed")


async def test_github_secondary_limit_without_retry_header(isolated: ConnectorConfig) -> None:
    http = ProviderHttp(
        isolated,
        httpx.MockTransport(
            lambda request: httpx.Response(
                403,
                json={"message": "You have exceeded a secondary rate limit."},
            )
        ),
    )
    with pytest.raises(ConnectorError) as error:
        await http.request("GET", "https://api.github.com/user")
    assert error.value.code == "rate_limited" and error.value.retry_after == 60
