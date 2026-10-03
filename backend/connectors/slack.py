"""Read-only Slack user OAuth; messages and thread pages are independent resources."""

import json
import re
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlsplit

from backend.connectors.base import OAuthConnector
from backend.connectors.core import ConnectorError, number, record, records, string
from backend.connectors.rates import reserve
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
from backend.schemas.pipeline import digest

ID = re.compile(r"[A-Z][A-Z0-9]{1,100}\Z")


class SlackConnector(OAuthConnector):
    provider: Provider = "slack"
    scopes = ("channels:read", "channels:history", "groups:read", "groups:history", "users:read", "files:read")
    authorization_endpoint = "https://slack.com/oauth/v2/authorize"
    exchange_endpoint = "https://slack.com/api/oauth.v2.access"
    api_root = "https://slack.com/api"

    async def get(
        self, path: str, params: dict[str, str | int] | None = None, *, token: str | None = None
    ) -> dict[str, object]:
        method = path.removeprefix("/")
        if method in {"conversations.history", "conversations.replies"} and self.account:
            await reserve(
                "slack:" + self.account.external_account_id + ":" + method, self.config.slack_history_interval_seconds
            )
        result = record(
            await self.http.request(
                "GET",
                self.api_root + path,
                params=params,
                token=token or await self.access_token(),
            )
        )
        if result.get("ok") is not True:
            error = result.get("error")
            code = (
                "reauth_required"
                if error in {"invalid_auth", "token_revoked", "token_expired", "account_inactive"}
                else "inaccessible"
                if error in {"not_in_channel", "channel_not_found", "access_denied", "missing_scope"}
                else "not_found"
                if error in {"message_not_found", "thread_not_found", "file_not_found"}
                else "rate_limited"
                if error == "ratelimited"
                else "provider_error"
            )
            raise ConnectorError(
                code,
                "Slack request failed; check authorization and selected resources",
                60 if code == "rate_limited" else 0,
            )
        await self.success()
        return result

    async def handle_callback(self, code: str, verifier: str) -> tuple[Credentials, str, str, dict[str, object]]:
        credentials = await self.exchange(code, verifier)
        identity = await self.get("/auth.test", token=credentials.access_token.get_secret_value())
        return (
            credentials,
            string(identity["team_id"]),
            string(identity["team"]),
            {"user_id": string(identity["user_id"])},
        )

    async def list_resources(self, cursor: str | None = None) -> ResourcePage:
        result = await self.get(
            "/conversations.list",
            {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": self.config.page_size,
                "cursor": cursor or "",
            },
        )
        refs = [
            Resource(
                id=string(channel["id"]),
                name=string(channel["name"]),
                kind="channel",
                metadata={"is_private": channel.get("is_private", False), "is_member": channel.get("is_member", False)},
            )
            for channel in records(result.get("channels", []))
        ]
        return ResourcePage(
            resources=refs,
            next_cursor=string(record(result.get("response_metadata", {})).get("next_cursor", "")) or None,
        )

    async def channel(self, identifier: str) -> dict[str, object]:
        if not ID.fullmatch(identifier):
            raise ConnectorError("invalid_resource", "Invalid Slack channel")
        channel = record((await self.get("/conversations.info", {"channel": identifier}))["channel"])
        if channel.get("is_private") and channel.get("is_member") is False:
            raise ConnectorError("inaccessible", "Join the selected private channel before synchronizing it")
        return channel

    def channel_access(self, channel: dict[str, object]) -> list[dict[str, object]]:
        return [
            {
                "type": "user",
                "id": (self.account.meta or {}).get("user_id", "") if self.account else "",
                "role": "reader",
                "channel_id": channel["id"],
                "is_private": channel.get("is_private"),
            }
        ]

    @staticmethod
    def selected_timestamp(value: str, selection: Selection) -> bool:
        created = datetime.fromtimestamp(float(value), UTC).date()
        return (selection.start_date is None or created >= selection.start_date) and (
            selection.end_date is None or created <= selection.end_date
        )

    def message_ref(self, channel: dict[str, object], message: dict[str, object]) -> Resource:
        ts = string(message["ts"])
        allowed = {
            key: message.get(key)
            for key in (
                "ts",
                "thread_ts",
                "user",
                "bot_id",
                "text",
                "edited",
                "subtype",
                "reply_count",
                "attachments",
                "files",
            )
            if key in message
        }
        return Resource(
            id=string(channel["id"]) + ":" + ts,
            name=string(channel["name"]) + " " + ts,
            kind="slack_message",
            parent_id=string(channel["id"]) + ":" + string(message.get("thread_ts", ts)),
            version=digest(allowed),
            metadata={
                "channel_id": channel["id"],
                "channel_name": channel["name"],
                "workspace_id": self.account.external_account_id if self.account else "",
                "message_ts": ts,
                "thread_ts": message.get("thread_ts", ts),
                "author_id": message.get("user", message.get("bot_id")),
                "created_at": datetime.fromtimestamp(float(ts), UTC).isoformat(),
                "is_private": channel.get("is_private", False),
                "object": allowed,
            },
        )

    async def initial_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        index = number(state.get("index", 0))
        anchor = string(state.get("anchor", str(datetime.now(UTC).timestamp())))
        if index >= len(selection.resource_ids):
            return SyncPage(complete=True, checkpoint={"since": anchor})
        channel = await self.channel(selection.resource_ids[index])
        oldest = string(state.get("since", "0"))
        if selection.start_date:
            oldest = str(max(float(oldest), datetime.combine(selection.start_date, time(), UTC).timestamp()))
        latest = anchor
        if selection.end_date:
            latest = str(
                min(float(anchor), datetime.combine(selection.end_date + timedelta(days=1), time(), UTC).timestamp())
            )
        if float(oldest) >= float(latest):
            return SyncPage(
                next_state={"index": index + 1, "anchor": anchor, "since": state.get("since", "0")},
                checkpoint={"since": anchor},
                complete=index + 1 >= len(selection.resource_ids),
            )
        result = await self.get(
            "/conversations.history",
            {
                "channel": string(channel["id"]),
                "limit": min(15, self.config.page_size, number(state.get("limit", self.config.page_size))),
                "oldest": oldest,
                "latest": latest,
                "cursor": string(state.get("page", "")),
            },
        )
        refs = [
            self.message_ref(channel, message)
            for message in records(result.get("messages", []))
            if message.get("type") == "message"
        ]
        cursor = string(record(result.get("response_metadata", {})).get("next_cursor", ""))
        if result.get("has_more") and not cursor:
            raise ConnectorError(
                "invalid_response", "Slack omitted its pagination cursor; synchronization was not completed"
            )
        next_index = index if cursor else index + 1
        return SyncPage(
            resources=refs,
            next_state={"index": next_index, "page": cursor, "anchor": anchor, "since": state.get("since", "0")},
            checkpoint={"since": anchor},
            complete=next_index >= len(selection.resource_ids),
        )

    async def incremental_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        return await self.initial_sync(selection, state)

    async def fetch_resource(self, resource: Resource, selection: Selection) -> FetchResult:
        channel_id = string(resource.metadata["channel_id"])
        if channel_id not in selection.resource_ids:
            raise ConnectorError("inaccessible", "Channel was not selected")
        channel = await self.channel(channel_id)
        if resource.kind == "slack_event_message":
            ts = string(resource.metadata["message_ts"])
            if not self.selected_timestamp(ts, selection):
                return FetchResult(removed_ids=[channel_id + ":" + ts])
            result = await self.get(
                "/conversations.history",
                {"channel": channel_id, "oldest": ts, "latest": ts, "inclusive": "true", "limit": 1},
            )
            messages = records(result.get("messages", []))
            if not messages:
                return FetchResult(removed_ids=[channel_id + ":" + ts])
            return await self.normalize(self.message_ref(channel, messages[0]), selection, include_thread=True)
        if resource.kind == "slack_thread_page":
            if not selection.threads:
                return FetchResult()
            result = await self.get(
                "/conversations.replies",
                {
                    "channel": channel_id,
                    "ts": string(resource.metadata["thread_ts"]),
                    "limit": min(15, self.config.page_size),
                    "cursor": string(resource.metadata.get("cursor", "")),
                },
            )
            envelopes = []
            followups = []
            removed = []
            for message in records(result.get("messages", [])):
                ref = self.message_ref(channel, message)
                ref.metadata["sync_group"] = "slack_thread:" + channel_id + ":" + string(resource.metadata["thread_ts"])
                fetched = await self.normalize(ref, selection, include_thread=False)
                envelopes.extend(fetched.envelopes)
                followups.extend(fetched.followups)
                removed.extend(fetched.removed_ids)
            cursor = string(record(result.get("response_metadata", {})).get("next_cursor", ""))
            if result.get("has_more") and not cursor:
                raise ConnectorError("invalid_response", "Slack omitted its thread pagination cursor")
            if cursor:
                followups.append(
                    resource.model_copy(
                        update={
                            "id": channel_id
                            + ":thread:"
                            + string(resource.metadata["thread_ts"])
                            + ":"
                            + digest(cursor),
                            "metadata": {**resource.metadata, "cursor": cursor},
                        }
                    )
                )
            return FetchResult(
                envelopes=envelopes,
                followups=followups,
                removed_ids=removed,
                completed_inventory_groups=[]
                if cursor
                else ["slack_thread:" + channel_id + ":" + string(resource.metadata["thread_ts"])],
            )
        if resource.kind == "slack_file":
            item = record((await self.get("/files.info", {"file": string(resource.metadata["file_id"])}))["file"])
            url = string(item.get("url_private_download", item.get("url_private", "")))
            parsed = urlsplit(url)
            if parsed.hostname not in {"files.slack.com", "slack.com"} or parsed.scheme != "https":
                raise ConnectorError("unsupported", "Slack file does not offer a supported private download")
            data = await self.http.request("GET", url, token=await self.access_token(), raw=True)
            if not isinstance(data, bytes):
                raise ConnectorError("invalid_response", "Invalid Slack file download")
            ref = resource.model_copy(
                update={
                    "metadata": {
                        **resource.metadata,
                        "filename": string(item["name"]),
                        "file_size": item.get("size"),
                        "mime_type": item.get("mimetype"),
                        "provider_channels": item.get("channels", []),
                    }
                }
            )
            return FetchResult(
                envelopes=[
                    await self.binary(
                        ref,
                        data,
                        string(item["name"]),
                        string(item.get("mimetype", "application/octet-stream")),
                        permissions=self.channel_access(channel),
                    )
                ]
            )
        return await self.normalize(resource, selection, include_thread=True)

    async def normalize(self, resource: Resource, selection: Selection, *, include_thread: bool) -> FetchResult:
        item = record(resource.metadata["object"])
        if not self.selected_timestamp(string(item["ts"]), selection):
            return FetchResult(removed_ids=[resource.id])
        metadata = {key: value for key, value in resource.metadata.items() if key != "object"}
        metadata["attachments"] = item.get("attachments", [])
        author = item.get("user")
        if isinstance(author, str) and author.startswith("U") and ID.fullmatch(author):
            try:
                user = record((await self.get("/users.info", {"user": author}))["user"])
                profile = record(user.get("profile", {}))
                metadata["author_name"] = (
                    profile.get("display_name") or user.get("real_name") or user.get("name") or author
                )
            except ConnectorError as error:
                if error.code in {"reauth_required", "rate_limited", "provider_unavailable"}:
                    raise
        metadata["files"] = [
            {key: file.get(key) for key in ("id", "name", "title", "mimetype", "size", "timestamp")}
            for file in records(item.get("files", []))
        ]
        raw = (
            "# "
            + resource.name
            + "\n\n"
            + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
            + "\n\n"
            + string(item.get("text", ""))
        )
        ref = resource.model_copy(update={"metadata": metadata})
        envelope = self.factory.text(
            ref,
            raw,
            "message.md",
            "text/markdown",
            permissions=self.channel_access({"id": metadata["channel_id"], "is_private": metadata["is_private"]}),
        )
        followups = []
        if include_thread and selection.threads and number(item.get("reply_count", 0)) > 0:
            followups.append(
                Resource(
                    id=string(metadata["channel_id"]) + ":thread:" + string(metadata["thread_ts"]),
                    name="Thread",
                    kind="slack_thread_page",
                    parent_id=resource.id,
                    metadata={"channel_id": metadata["channel_id"], "thread_ts": metadata["thread_ts"]},
                )
            )
        if selection.files:
            followups.extend(
                Resource(
                    id="file:" + string(file["id"]) + ":" + resource.id,
                    name=string(file.get("name", "attachment")),
                    kind="slack_file",
                    parent_id=resource.id,
                    version=digest(file),
                    metadata={
                        "channel_id": metadata["channel_id"],
                        "file_id": file["id"],
                        "parent_message": resource.id,
                    },
                )
                for file in records(metadata["files"])
            )
        return FetchResult(envelopes=[envelope], followups=followups)

    async def handle_event(self, event: dict[str, object]) -> list[Resource]:
        item = record(event.get("event", {}))
        channel_id = item.get("channel")
        if (
            not isinstance(channel_id, str)
            or not self.account
            or channel_id not in Selection.model_validate(self.account.configuration).resource_ids
        ):
            return []
        message = record(item.get("message", {})) if item.get("subtype") == "message_changed" else item
        ts = message.get("ts", item.get("deleted_ts"))
        if not isinstance(ts, str) or not re.fullmatch(r"[0-9]{1,20}\.[0-9]{1,10}", ts):
            return []
        if message.get("thread_ts") and message.get("thread_ts") != ts:
            thread = string(message["thread_ts"])
            return [
                Resource(
                    id=channel_id + ":thread:" + thread,
                    name="Thread",
                    kind="slack_thread_page",
                    metadata={"channel_id": channel_id, "thread_ts": thread},
                )
            ]
        return [
            Resource(
                id=channel_id + ":event:" + ts,
                name="Changed message",
                kind="slack_event_message",
                metadata={"channel_id": channel_id, "message_ts": ts},
            )
        ]

    async def structured_query(self, query: QuerySpec, selection: Selection) -> QueryResult:
        if query.sender or query.recipient:
            raise ConnectorError("invalid_query", "Slack structured queries support channel and date filters")
        channel_id = query.resource_id
        if channel_id not in selection.resource_ids or channel_id is None:
            raise ConnectorError("inaccessible", "Select a configured channel for this query")
        bounded = query.selected_window(selection)
        if bounded is None:
            return QueryResult(resources=[], count=0, exact=True)
        bounded = bounded.model_copy(update={"resource_ids": [channel_id]})
        state: dict[str, object] = {"page": query.cursor or "", "limit": query.limit}
        page = await self.initial_sync(bounded, state)
        cursor = string(page.next_state.get("page", "")) or None
        return QueryResult(
            resources=page.resources if query.operation == "list" else [],
            count=len(page.resources),
            exact=not cursor,
            next_cursor=cursor,
        )

    async def health_check(self) -> bool:
        await self.get("/auth.test")
        return True
