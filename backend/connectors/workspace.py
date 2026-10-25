"""Native workspace tools. Provider HTTP/credentials end here, before canonical parsing."""

import base64
import hashlib
import mimetypes
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID

from backend.connectors.core import ConnectorError, record, records, string, strings, unbase64
from backend.connectors.drive import FILE_FIELDS, FOLDER, GoogleDriveConnector, drive_id, file_resource
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.slack import SlackConnector
from backend.schemas.agent import DraftEmail, PrepareFile, ReadProvider, SearchProvider, email_address
from backend.schemas.connectors import FetchResult, QuerySpec, Resource, Selection
from backend.schemas.github_evidence import GitHubEvidenceRequest, GitHubEvidenceSnapshot
from backend.services.uploads import safe_filename

SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def headers(message: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in records(record(message.get("payload", {})).get("headers", [])):
        result.setdefault(string(header["name"]).lower(), string(header["value"]))
    return result


def safe_path(path: str | None) -> str:
    if not path or len(path) > 1000 or path.startswith("/") or "\\" in path or any(c in path for c in "\r\n\x00"):
        raise ConnectorError("invalid_resource", "Choose a repository-relative file path")
    if any(piece in {".", "..", ""} for piece in path.split("/")):
        raise ConnectorError("invalid_resource", "Invalid repository file path")
    return path


class NativeWorkspace:
    def __init__(
        self, connector: GmailConnector | GoogleDriveConnector | GitHubConnector | SlackConnector, selection: Selection
    ) -> None:
        self.connector, self.selection = connector, selection

    async def drive_file(self, resource_id: str) -> dict[str, object]:
        connector = self.connector
        if not isinstance(connector, GoogleDriveConnector):
            raise ConnectorError("unsupported", "This tool requires a Drive account")
        identifier = drive_id(resource_id)
        file = await connector.get("/files/" + identifier, {"fields": FILE_FIELDS, "supportsAllDrives": "true"})
        if file.get("trashed") is True or not connector.matches_type(file_resource(file), self.selection):
            raise ConnectorError("inaccessible", "File is removed or outside the selected types")
        selected = set(self.selection.resource_ids)
        allowed = "file:" + identifier in selected
        shared = file.get("driveId")
        if isinstance(shared, str) and "drive:" + shared in selected:
            await connector.get("/drives/" + drive_id(shared))
            allowed = True
        queue = strings(file.get("parents", [])).copy()
        visited: set[str] = set()
        while not allowed and queue and len(visited) < 64:
            parent = queue.pop(0)
            if parent in visited:
                continue
            visited.add(parent)
            value = await connector.get(
                "/files/" + drive_id(parent), {"fields": "id,mimeType,parents,trashed", "supportsAllDrives": "true"}
            )
            if value.get("trashed") is True:
                continue
            if "folder:" + parent in selected and value.get("mimeType") == FOLDER:
                allowed = True
            else:
                queue.extend(strings(value.get("parents", [])))
        if not allowed:
            raise ConnectorError("inaccessible", "File is outside the selected Drive roots")
        return file

    async def gmail_message(self, resource_id: str, *, full: bool = False) -> dict[str, object]:
        connector = self.connector
        if not isinstance(connector, GmailConnector) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", resource_id):
            raise ConnectorError("invalid_resource", "Choose a Gmail message ID")
        message = await connector.get("/messages/" + resource_id, {"format": "full" if full else "metadata"})
        if not connector.selected_message(message, self.selection):
            raise ConnectorError("inaccessible", "Email is outside the selected labels or dates")
        return message

    async def github_repository(self, resource_id: str) -> tuple[dict[str, object], str, str]:
        connector = self.connector
        if not isinstance(connector, GitHubConnector) or resource_id not in self.selection.resource_ids:
            raise ConnectorError("inaccessible", "Repository is outside the selected installation/user access")
        repo = await connector.repository(resource_id)
        token = await connector.installation_token(repo, self.selection)
        return repo, string(repo["full_name"]), token

    async def search(self, args: SearchProvider) -> dict[str, object]:
        connector = self.connector
        if isinstance(connector, GmailConnector):
            label = args.resource_id
            if not self.selection.resource_ids or (label and label not in self.selection.resource_ids):
                raise ConnectorError("inaccessible", "Choose a selected Gmail label")
            terms = [connector.date_query(self.selection), args.query]
            params: dict[str, str | int] = {"q": " ".join(terms), "maxResults": args.limit}
            if label:
                params["labelIds"] = label
            if args.cursor:
                params["pageToken"] = args.cursor
            result = await connector.get("/messages", params)
            output = []
            for item in records(result.get("messages", [])):
                try:
                    message = await self.gmail_message(string(item["id"]))
                except ConnectorError as error:
                    if error.code in {"inaccessible", "not_found"}:
                        continue
                    raise
                values = headers(message)
                output.append(
                    {
                        "id": item["id"],
                        "thread_id": message.get("threadId"),
                        "subject": values.get("subject"),
                        "from": values.get("from"),
                        "date": values.get("date"),
                        "snippet": message.get("snippet"),
                    }
                )
            return {
                "resources": output,
                "next_cursor": result.get("nextPageToken"),
                "complete": not result.get("nextPageToken"),
            }
        if isinstance(connector, GoogleDriveConnector):
            term = args.query.replace("\\", "\\\\").replace("'", "\\'")
            query = "trashed=false and mimeType!='application/vnd.google-apps.folder'"
            if term:
                query += f" and (name contains '{term}' or fullText contains '{term}')"
            params = {
                "q": query,
                "fields": f"nextPageToken,files({FILE_FIELDS})",
                "pageSize": args.limit,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if args.cursor:
                params["pageToken"] = args.cursor
            result = await connector.get("/files", params)
            output = []
            for file in records(result.get("files", [])):
                try:
                    authorized = await self.drive_file(string(file["id"]))
                except ConnectorError as error:
                    if error.code in {"inaccessible", "not_found"}:
                        continue
                    raise
                output.append(
                    {
                        "id": "file:" + string(authorized["id"]),
                        "name": authorized.get("name"),
                        "mime_type": authorized.get("mimeType"),
                        "version": authorized.get("version"),
                    }
                )
            return {
                "resources": output,
                "next_cursor": result.get("nextPageToken"),
                "complete": not result.get("nextPageToken"),
                "scope": "selected roots only; bounded discovery page",
            }
        if isinstance(connector, GitHubConnector):
            selected = [args.resource_id] if args.resource_id else self.selection.resource_ids
            offset = int(args.cursor or "0") if (args.cursor or "0").isdigit() else -1
            if offset < 0 or offset > len(selected):
                raise ConnectorError("invalid_query", "Invalid repository page cursor")
            output = []
            for identifier in selected[offset : offset + args.limit]:
                repo, name, _ = await self.github_repository(identifier)
                output.append({"id": identifier, "name": name, "description": repo.get("description")})
            end = offset + args.limit
            return {
                "resources": output,
                "next_cursor": str(end) if end < len(selected) else None,
                "complete": end >= len(selected),
            }
        page = await connector.structured_query(
            QuerySpec(resource_id=args.resource_id, limit=args.limit, cursor=args.cursor), self.selection
        )
        return page.model_dump(mode="json")

    async def github_evidence(self, args: GitHubEvidenceRequest) -> GitHubEvidenceSnapshot:
        if not isinstance(self.connector, GitHubConnector):
            raise ConnectorError("unsupported", "Use a GitHub account for milestone evidence")
        return await self.connector.github_evidence(args, self.selection)

    async def github_status(self, args: ReadProvider) -> dict[str, object]:
        connector = self.connector
        if not isinstance(connector, GitHubConnector):
            raise ConnectorError("unsupported", "Use a GitHub account")
        repo, name, token = await self.github_repository(args.resource_id)
        branch = self.selection.branches.get(args.resource_id) or string(repo.get("default_branch", "main"))
        output: dict[str, object] = {
            "repository": name,
            "branch": branch,
            "archived": repo.get("archived"),
            "observed_at": datetime.now(UTC).isoformat(),
            "scope": "bounded pages, not total counts",
        }
        categories = self.selection.categories
        if "commits" in categories or "code" in categories:
            try:
                commits = await connector.array(f"/repos/{name}/commits", {"sha": branch, "per_page": 5}, token=token)
                output["commits"] = [
                    {
                        "sha": item.get("sha"),
                        "message": record(item.get("commit", {})).get("message"),
                        "url": item.get("html_url"),
                    }
                    for item in commits
                ]
            except ConnectorError as error:
                if error.code not in {"not_found", "provider_error"}:
                    raise
                output["commits"] = []
                output["warning"] = "Commit history is unavailable; repository may be empty"
        for category, endpoint in [("issues", "issues"), ("pull_requests", "pulls"), ("releases", "releases")]:
            if category in categories:
                params: dict[str, str | int] = {"per_page": 5}
                if category != "releases":
                    params.update(state="all", sort="updated", direction="desc")
                items = await connector.array(f"/repos/{name}/{endpoint}", params, token=token)
                output[category] = [
                    {
                        key: item.get(key)
                        for key in (
                            "number",
                            "title",
                            "name",
                            "state",
                            "draft",
                            "merged_at",
                            "updated_at",
                            "html_url",
                            "tag_name",
                        )
                    }
                    for item in items
                    if category != "issues" or "pull_request" not in item
                ]
        return output

    async def resource(self, args: ReadProvider) -> Resource:
        connector = self.connector
        if isinstance(connector, GoogleDriveConnector):
            file = await self.drive_file(args.resource_id)
            if record(file.get("capabilities", {})).get("canDownload") is False:
                raise ConnectorError("inaccessible", "The owner disabled content downloads")
            return file_resource(file)
        if isinstance(connector, GmailConnector):
            message = await self.gmail_message(args.resource_id)
            return Resource(
                id=args.resource_id,
                name=headers(message).get("subject", "Email"),
                kind="email_message",
                version=str(message.get("historyId", "unknown")),
            )
        if isinstance(connector, GitHubConnector):
            if "code" not in self.selection.categories:
                raise ConnectorError("inaccessible", "Code access was not selected")
            repo, name, token = await self.github_repository(args.resource_id)
            path = safe_path(args.path)
            revision = (
                args.revision
                or self.selection.branches.get(args.resource_id)
                or string(repo.get("default_branch", "main"))
            )
            if not re.fullmatch(r"[a-zA-Z0-9._/-]{1,100}", revision) or ".." in revision:
                raise ConnectorError("invalid_resource", "Invalid repository revision")
            selected_branch = self.selection.branches.get(args.resource_id) or string(
                repo.get("default_branch", "main")
            )
            if revision != selected_branch and not re.fullmatch(r"[a-f0-9]{40}", revision):
                raise ConnectorError("inaccessible", "Choose the selected branch or an observed commit")
            branch = await connector.get(f"/repos/{name}/commits/{quote(selected_branch, safe='')}", token=token)
            head = string(branch["sha"])
            if revision != selected_branch and revision != head:
                raise ConnectorError("inaccessible", "Commit is not the current selected branch head")
            file = await connector.get(f"/repos/{name}/contents/{quote(path, safe='/')}", {"ref": head}, token=token)
            if file.get("type") != "file" or file.get("encoding") not in {"base64", None}:
                raise ConnectorError("unsupported", "Choose a regular repository file")
            return Resource(
                id=f"{args.resource_id}:file:{path}",
                name=path,
                kind="source_file",
                version=string(file["sha"]),
                metadata={
                    "repository_id": args.resource_id,
                    "file_path": path,
                    "blob_sha": file["sha"],
                    "commit_sha": head,
                    "branch": selected_branch,
                },
            )
        raise ConnectorError("unsupported", "Read Slack messages through indexed snapshots or channel queries")

    async def check_reference(self, reference: dict[str, object]) -> None:
        if reference.get("status_request"):
            args = ReadProvider.model_validate(reference["status_request"])
            await self.github_repository(args.resource_id)
        if reference.get("search_request"):
            identifiers = strings(reference.get("resource_ids", []))
            for identifier in identifiers:
                if isinstance(self.connector, GmailConnector):
                    await self.gmail_message(identifier)
                elif isinstance(self.connector, GoogleDriveConnector):
                    await self.drive_file(identifier)
                elif isinstance(self.connector, GitHubConnector):
                    await self.github_repository(identifier)
                else:
                    channel = identifier.split(":", 1)[0]
                    if channel not in self.selection.resource_ids:
                        raise ConnectorError("inaccessible", "Slack channel is outside the selected resources")
                    await self.connector.channel(channel)

    async def fetch(self, args: ReadProvider) -> tuple[FetchResult, dict[str, object]]:
        resource = await self.resource(args)
        result = await self.connector.fetch_resource(resource, self.selection)
        return result, {
            "resource_id": args.resource_id,
            "path": args.path,
            "revision": args.revision,
            "version": resource.version,
            "provider": self.connector.provider,
        }

    async def file_info(self, args: PrepareFile) -> tuple[str, str, dict[str, object]]:
        resource = await self.resource(args)
        if self.connector.provider == "gmail":
            raise ConnectorError("unsupported", "Choose a file from Drive or GitHub")
        if self.connector.provider == "google_drive":
            mime = string(resource.metadata["mime_type"])
            if mime.startswith("application/vnd.google-apps."):
                if args.format != "pdf" or mime not in {
                    "application/vnd.google-apps.document",
                    "application/vnd.google-apps.spreadsheet",
                    "application/vnd.google-apps.presentation",
                }:
                    raise ConnectorError("unsupported", "Google Workspace files support PDF export in this tool")
                filename, mime = PurePosixPath(resource.name).stem + ".pdf", "application/pdf"
            else:
                filename = resource.name
                if args.format == "pdf" and mime != "application/pdf":
                    raise ConnectorError("unsupported", "This existing file is not a PDF; choose original format")
        else:
            filename = PurePosixPath(resource.name).name
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            if args.format == "pdf" and mime != "application/pdf":
                raise ConnectorError("unsupported", "Choose an existing PDF from the selected repository")
        return (
            safe_filename(filename),
            mime,
            {
                "resource_id": args.resource_id,
                "version": resource.version,
                "path": args.path,
                "revision": args.revision,
            },
        )

    async def file_bytes(self, args: PrepareFile, reference: dict[str, object]) -> bytes:
        _, mime, current = await self.file_info(args)
        if current["version"] != reference.get("version"):
            raise ConnectorError("processing", "File changed; prepare a new download")
        connector = self.connector
        if isinstance(connector, GoogleDriveConnector):
            file = await self.drive_file(args.resource_id)
            if record(file.get("capabilities", {})).get("canDownload") is False:
                raise ConnectorError("inaccessible", "The owner disabled downloads")
            path = "/files/" + drive_id(args.resource_id)
            data = (
                await connector.download(path + "/export", {"mimeType": "application/pdf"})
                if (string(file["mimeType"]).startswith("application/vnd.google-apps."))
                else await connector.download(path, {"alt": "media", "supportsAllDrives": "true"})
            )
        elif isinstance(connector, GitHubConnector):
            resource = await self.resource(args)
            _, name, token = await self.github_repository(args.resource_id)
            blob = await connector.get(f"/repos/{name}/git/blobs/{resource.version}", token=token)
            data = unbase64(string(blob["content"]).replace("\n", ""))
            identity = hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
            ).hexdigest()
            if identity != resource.version:
                raise ConnectorError("invalid_response", "Repository download does not match the pinned blob identity")
        else:
            raise ConnectorError("unsupported", "Unsupported file provider")
        if not data or len(data) > 10 * 1024 * 1024 or (mime == "application/pdf" and not data.startswith(b"%PDF-")):
            raise ConnectorError("too_large", "File is empty, invalid or exceeds the 10 MiB workflow download limit")
        # Recheck selection and identity after download as well as before it.
        _, _, final = await self.file_info(args)
        if final["version"] != current["version"]:
            raise ConnectorError("processing", "File changed during download; prepare it again")
        return data

    async def email_payload(self, draft: DraftEmail) -> dict[str, object]:
        connector = self.connector
        if not isinstance(connector, GmailConnector):
            raise ConnectorError("unsupported", "Choose Gmail for email actions")
        payload = draft.model_dump(mode="json")
        payload["thread_id"], payload["in_reply_to"], payload["references"] = None, None, None
        if draft.reply_message_id:
            original = await self.gmail_message(draft.reply_message_id)
            values = headers(original)
            recipient = email_address(parseaddr(values.get("from", ""), strict=True)[1])
            if draft.recipient != recipient:
                raise ConnectorError("inaccessible", "A reply must target the verified original sender")
            message_id = values.get("message-id", "")
            thread_id = string(original.get("threadId"))
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", thread_id):
                raise ConnectorError("invalid_response", "The original Gmail thread identity is invalid")
            if not re.fullmatch(r"<[^<>\s\r\n]{3,250}>", message_id):
                raise ConnectorError("unsupported", "The original email has no usable Message-ID for a threaded reply")
            subject = values.get("subject", "")
            if any(c in subject for c in "\r\n\x00") or not subject or len(subject) > 195:
                raise ConnectorError("unsupported", "Original subject requires manual review")
            payload.update(
                subject=subject if subject.lower().startswith("re:") else "Re: " + subject,
                thread_id=thread_id,
                in_reply_to=message_id,
                references=message_id,
                original_version=str(original.get("historyId", "unknown")),
            )
        return payload

    async def send_email(
        self, payload: dict[str, object], action_id: UUID, *, before_send: Callable[[], Awaitable[None]]
    ) -> str:
        connector = self.connector
        if not isinstance(connector, GmailConnector) or connector.account is None:
            raise ConnectorError("unsupported", "Use a connected Gmail account")
        if not connector.config.gmail_send_enabled or SEND_SCOPE not in connector.account.scopes:
            raise ConnectorError(
                "missing_scope", "Enable Gmail sending on the server and reconnect to grant gmail.send"
            )
        draft = DraftEmail.model_validate({key: payload[key] for key in DraftEmail.model_fields})
        fresh = await self.email_payload(draft)
        if any(
            fresh.get(key) != payload.get(key)
            for key in ("recipient", "subject", "thread_id", "in_reply_to", "original_version")
        ):
            raise ConnectorError("processing", "Original email changed; review a fresh draft")
        profile = await connector.get("/profile")
        message = EmailMessage(policy=SMTP)
        message["From"], message["To"], message["Subject"] = (
            email_address(string(profile["emailAddress"])),
            draft.recipient,
            string(payload["subject"]),
        )
        message["Message-ID"] = f"<fixflow.{action_id}@fixflow.local>"
        if payload.get("in_reply_to"):
            message["In-Reply-To"], message["References"] = (
                string(payload["in_reply_to"]),
                string(payload["references"]),
            )
        message.set_content(draft.body)
        body: dict[str, object] = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
        if payload.get("thread_id"):
            body["threadId"] = payload["thread_id"]
        token = await connector.access_token()
        await before_send()
        try:
            result = record(
                await connector.http.request(
                    "POST", connector.api_root + "/messages/send", token=token, payload=body, retry=False
                )
            )
            message_id = string(result["id"])
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", message_id):
                raise ValueError("Invalid Gmail delivery identity")
            return message_id
        except ConnectorError as error:
            if error.code in {"reauth_required", "inaccessible", "not_found", "rate_limited", "missing_scope"}:
                raise
            raise ConnectorError(
                "delivery_unknown", "Gmail delivery could not be confirmed; no automatic resend"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ConnectorError(
                "delivery_unknown", "Gmail delivery receipt was invalid; no automatic resend"
            ) from error
