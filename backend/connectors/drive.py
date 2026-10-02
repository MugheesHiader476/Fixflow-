"""Drive v3 changes and structure-preserving Workspace exports, including shared drives."""

import json
from pathlib import PurePosixPath
from urllib.parse import quote

from backend.connectors.base import OAuthConnector
from backend.connectors.core import ConnectorError, number, record, records, string, strings
from backend.schemas.connectors import (
    Credentials,
    FetchResult,
    Provider,
    QueryResult,
    QuerySpec,
    Resource,
    ResourcePage,
    Selection,
    SyncPage,
)

FILE_FIELDS = (
    "id,name,mimeType,version,createdTime,modifiedTime,owners,parents,driveId,"
    "permissions,trashed,capabilities(canDownload)"
)
EXPORTS = {
    "application/vnd.google-apps.document": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "application/vnd.google-apps.spreadsheet": (
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "application/vnd.google-apps.presentation": (
        ".pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
}
FOLDER = "application/vnd.google-apps.folder"


def file_resource(file: dict[str, object]) -> Resource:
    identifier, mime = string(file["id"]), string(file["mimeType"])
    return Resource(
        id=("folder:" if mime == FOLDER else "file:") + identifier,
        name=string(file["name"]),
        kind="folder" if mime == FOLDER else "drive_file",
        version=string(file.get("version", file.get("modifiedTime", "unknown"))),
        parent_id=next(iter(strings(file.get("parents", []))), None),
        metadata={
            "file_id": identifier,
            "mime_type": mime,
            "parents": file.get("parents", []),
            "shared_drive": file.get("driveId"),
            "owners": file.get("owners", []),
            "created_at": file.get("createdTime"),
            "updated_at": file.get("modifiedTime"),
            "provider_permissions": file.get("permissions", []),
            "can_download": record(file.get("capabilities", {})).get("canDownload"),
        },
    )


def drive_id(value: str) -> str:
    identifier = value.split(":", 1)[-1]
    if not identifier or len(identifier) > 300 or not all(c.isalnum() or c in "_-" for c in identifier):
        raise ConnectorError("invalid_resource", "Invalid Drive resource identifier")
    return identifier


class GoogleDriveConnector(OAuthConnector):
    provider: Provider = "google_drive"
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    exchange_endpoint = "https://oauth2.googleapis.com/token"
    api_root = "https://www.googleapis.com/drive/v3"
    scopes = (
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/drive.readonly",
    )

    async def handle_callback(self, code: str, verifier: str) -> tuple[Credentials, str, str, dict[str, object]]:
        credentials = await self.exchange(code, verifier)
        identity = record(
            await self.http.request(
                "GET",
                "https://openidconnect.googleapis.com/v1/userinfo",
                token=credentials.access_token.get_secret_value(),
            )
        )
        await self.get("/about", {"fields": "user"}, token=credentials.access_token.get_secret_value())
        return credentials, string(identity["sub"]), string(identity["email"]), {"email": string(identity["email"])}

    async def list_resources(self, cursor: str | None = None, *, limit: int | None = None) -> ResourcePage:
        state = record(json.loads(cursor)) if cursor else {"phase": "drives"}
        params: dict[str, str | int] = {"pageSize": min(self.config.page_size, limit or self.config.page_size)}
        if state.get("token"):
            params["pageToken"] = string(state["token"])
        if state.get("phase") == "drives":
            result = await self.get("/drives", params)
            refs = [
                Resource(id="drive:" + string(d["id"]), name=string(d["name"]), kind="shared_drive")
                for d in records(result.get("drives", []))
            ]
            next_state = (
                {"phase": "drives", "token": result["nextPageToken"]}
                if result.get("nextPageToken")
                else {"phase": "files"}
            )
            return ResourcePage(resources=refs, next_cursor=json.dumps(next_state))
        params.update(
            q="trashed=false",
            fields=f"nextPageToken,files({FILE_FIELDS})",
            supportsAllDrives="true",
            includeItemsFromAllDrives="true",
        )
        result = await self.get("/files", params)
        token = result.get("nextPageToken")
        return ResourcePage(
            resources=[file_resource(f) for f in records(result.get("files", []))],
            next_cursor=json.dumps({"phase": "files", "token": token}) if token else None,
        )

    async def initial_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        start = (
            state.get("start_token")
            or (await self.get("/changes/startPageToken", {"supportsAllDrives": "true"}))["startPageToken"]
        )
        index = number(state.get("index", 0))
        known = strings(state.get("folders", []))
        queue = strings(state.get("folder_queue", []))
        if not queue and index >= len(selection.resource_ids):
            return SyncPage(complete=True, checkpoint={"page_token": start, "folders": known})
        if not queue:
            queue = [selection.resource_ids[index]]
            index += 1
        current = queue[0]
        identifier = drive_id(current)
        if current.startswith("file:"):
            file = await self.get(
                "/files/" + quote(identifier, safe=""), {"fields": FILE_FIELDS, "supportsAllDrives": "true"}
            )
            next_state = {"start_token": start, "index": index, "folders": known, "folder_queue": queue[1:]}
            ref = file_resource(file)
            return SyncPage(
                resources=[ref] if self.matches_type(ref, selection) else [],
                next_state=next_state,
                checkpoint={"page_token": start, "folders": known},
                complete=index >= len(selection.resource_ids) and len(queue) == 1,
            )
        params: dict[str, str | int] = {
            "pageSize": self.config.page_size,
            "fields": f"nextPageToken,files({FILE_FIELDS})",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "q": "trashed=false",
        }
        if current.startswith("drive:"):
            params.update(corpora="drive", driveId=identifier)
        else:
            # Query syntax is constructed only from validated IDs, never raw user query text.
            params["q"] = f"trashed=false and '{identifier}' in parents"
            known = list(dict.fromkeys([*known, identifier]))
        if state.get("list_token"):
            params["pageToken"] = string(state["list_token"])
        result = await self.get("/files", params)
        refs = [file_resource(file) for file in records(result.get("files", []))]
        children = [ref.id for ref in refs if ref.kind == "folder" and drive_id(ref.id) not in known]
        known = list(dict.fromkeys([*known, *(drive_id(ref.id) for ref in refs if ref.kind == "folder")]))
        if len(known) > self.config.max_sync_resources:
            raise ConnectorError("too_large", "Selected folder tree exceeds the configured synchronization limit")
        token = result.get("nextPageToken")
        remaining = queue.copy() if token else queue[1:]
        # Shared drive listing already covers descendants; folder selections need breadth-first traversal.
        if not current.startswith("drive:"):
            remaining = list(dict.fromkeys([*remaining, *children]))
        next_state = {
            "start_token": start,
            "index": index,
            "list_token": token or "",
            "folder_queue": remaining,
            "folders": known,
        }
        return SyncPage(
            resources=[ref for ref in refs if ref.kind != "folder" and self.matches_type(ref, selection)],
            next_state=next_state,
            checkpoint={"page_token": start, "folders": known},
            complete=not token and not remaining and index >= len(selection.resource_ids),
        )

    @staticmethod
    def matches_type(resource: Resource, selection: Selection) -> bool:
        return not selection.mime_types or resource.metadata.get("mime_type") in selection.mime_types

    async def incremental_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        marker = state.get("page_token")
        if not marker:
            return await self.initial_sync(selection, {})
        params: dict[str, str | int] = {
            "pageToken": string(marker),
            "pageSize": self.config.page_size,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
        }
        try:
            result = await self.get("/changes", params)
        except ConnectorError as error:
            if error.code == "stale_cursor":
                return (await self.initial_sync(selection, {})).model_copy(update={"reconcile": True})
            raise
        folders = strings(state.get("folders", []))
        selected_drives = {drive_id(value) for value in selection.resource_ids if value.startswith("drive:")}
        refs: list[Resource] = []
        removed: list[str] = []
        for change in records(result.get("changes", [])):
            identifier = "file:" + string(change["fileId"])
            if change.get("removed") is True:
                if string(change["fileId"]) in folders:
                    return (await self.initial_sync(selection, {})).model_copy(update={"reconcile": True})
                removed.append(identifier)
                continue
            file = record(change["file"])
            ref = file_resource(file)
            if ref.kind == "folder" and (string(file["id"]) in folders or file.get("driveId") in selected_drives):
                return (await self.initial_sync(selection, {})).model_copy(update={"reconcile": True})
            eligible = (
                identifier in selection.resource_ids
                or bool(set(strings(file.get("parents", []))) & set(folders))
                or file.get("driveId") in selected_drives
            )
            if file.get("trashed") is True or not eligible or not self.matches_type(ref, selection):
                removed.append(identifier)
            elif ref.kind == "folder":
                # A new/moved folder changes the selected subtree; a resumable reconciliation discovers descendants.
                return (await self.initial_sync(selection, {})).model_copy(update={"reconcile": True})
            else:
                refs.append(ref)
        token = result.get("nextPageToken") or result.get("newStartPageToken")
        if not token:
            raise ConnectorError("invalid_response", "Drive change feed omitted its continuation token")
        checkpoint = {"page_token": token, "folders": folders}
        return SyncPage(
            resources=refs,
            deleted_ids=removed,
            next_state=checkpoint,
            checkpoint=checkpoint,
            complete=not result.get("nextPageToken"),
        )

    async def fetch_resource(self, resource: Resource, selection: Selection) -> FetchResult:
        identifier = drive_id(resource.id)
        file = await self.get(
            "/files/" + quote(identifier, safe=""), {"fields": FILE_FIELDS, "supportsAllDrives": "true"}
        )
        if file.get("trashed") is True:
            return FetchResult(removed_ids=[resource.id])
        capabilities = record(file.get("capabilities", {}))
        if capabilities.get("canDownload") is False:
            raise ConnectorError("inaccessible", "Drive file owner disabled downloads")
        ref = file_resource(file)
        mime = string(file["mimeType"])
        permissions = records(file.get("permissions", []))
        if file.get("driveId"):
            permissions = []
            params: dict[str, str | int] = {
                "supportsAllDrives": "true",
                "fields": "nextPageToken,permissions(id,type,role,emailAddress,domain)",
                "pageSize": 100,
            }
            while True:
                result = await self.get("/files/" + quote(identifier, safe="") + "/permissions", params)
                permissions.extend(records(result.get("permissions", [])))
                if len(permissions) > self.config.max_sync_resources:
                    raise ConnectorError("too_large", "Drive permission list exceeds the configured limit")
                if not result.get("nextPageToken"):
                    break
                params["pageToken"] = string(result["nextPageToken"])
            ref.metadata["provider_permissions"] = permissions
        name = string(file["name"])
        if mime in EXPORTS:
            extension, export_mime = EXPORTS[mime]
            filename = PurePosixPath(name).stem + extension
            data = await self.download("/files/" + quote(identifier, safe="") + "/export", {"mimeType": export_mime})
            ref.metadata["original_mime_type"] = mime
            mime = export_mime
        else:
            filename = name
            data = await self.download(
                "/files/" + quote(identifier, safe=""), {"alt": "media", "supportsAllDrives": "true"}
            )
        return FetchResult(envelopes=[await self.binary(ref, data, filename, mime, permissions=permissions)])

    async def handle_event(self, event: dict[str, object]) -> list[Resource]:
        return []  # Channel notifications wake the authoritative changes feed.

    async def structured_query(self, query: QuerySpec, selection: Selection) -> QueryResult:
        if query.sender or query.recipient or query.start_date or query.end_date:
            raise ConnectorError("invalid_query", "Drive structured queries support selected resource discovery only")
        selected = [query.resource_id] if query.resource_id else selection.resource_ids
        if not set(selected) <= set(selection.resource_ids):
            raise ConnectorError("inaccessible", "Query references an unselected Drive resource")
        page = await self.list_resources(query.cursor, limit=query.limit)
        resources = [ref for ref in page.resources if ref.id in selected]
        return QueryResult(
            resources=resources if query.operation == "list" else [],
            count=len(resources),
            exact=page.next_cursor is None,
            next_cursor=page.next_cursor,
        )

    async def health_check(self) -> bool:
        await self.get("/about", {"fields": "user"})
        return True
