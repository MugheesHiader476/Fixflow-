"""Read-only Gmail labels, history, messages and individually traceable attachments."""

import hashlib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import quote

from backend.connectors.base import OAuthConnector
from backend.connectors.core import ConnectorError, number, record, records, string, strings, unbase64
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


class GmailConnector(OAuthConnector):
    provider: Provider = "gmail"
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    exchange_endpoint = "https://oauth2.googleapis.com/token"
    api_root = "https://gmail.googleapis.com/gmail/v1/users/me"
    scopes = (
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.readonly",
    )

    async def handle_callback(self, code: str, verifier: str) -> tuple[Credentials, str, str, dict[str, object]]:
        credentials = await self.exchange(code, verifier)
        token = credentials.access_token.get_secret_value()
        identity = record(
            await self.http.request("GET", "https://openidconnect.googleapis.com/v1/userinfo", token=token)
        )
        profile = await self.get("/profile", token=token)
        email = string(profile["emailAddress"])
        return credentials, string(identity["sub"]), email, {"email": email}

    async def list_resources(self, cursor: str | None = None) -> ResourcePage:
        result = await self.get("/labels")
        return ResourcePage(
            resources=[
                Resource(id=string(label["id"]), name=string(label["name"]), kind="label")
                for label in records(result.get("labels", []))
            ]
        )

    @staticmethod
    def date_query(selection: Selection) -> str:
        terms = []
        if selection.start_date:
            terms.append(
                "after:" + str(int(datetime.combine(selection.start_date, datetime.min.time(), UTC).timestamp()))
            )
        if selection.end_date:
            end = datetime.combine(selection.end_date + timedelta(days=1), datetime.min.time(), UTC)
            terms.append("before:" + str(int(end.timestamp())))
        return " ".join(terms)

    async def initial_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        index = number(state.get("label_index", 0))
        if index >= len(selection.resource_ids):
            return SyncPage(complete=True, checkpoint={"history_id": state.get("history_id", "")})
        history = state.get("history_id") or (await self.get("/profile"))["historyId"]
        params: dict[str, str | int] = {
            "maxResults": self.config.page_size,
            "labelIds": selection.resource_ids[index],
            "q": self.date_query(selection),
        }
        if state.get("page_token"):
            params["pageToken"] = string(state["page_token"])
        result = await self.get("/messages", params)
        next_token = result.get("nextPageToken")
        next_state = {
            "history_id": history,
            "label_index": index if next_token else index + 1,
            "page_token": next_token or "",
        }
        return SyncPage(
            resources=[
                Resource(
                    id=string(item["id"]),
                    name=string(item["id"]),
                    kind="email_message",
                    parent_id=string(item["threadId"]),
                )
                for item in records(result.get("messages", []))
            ],
            next_state=next_state,
            checkpoint={"history_id": history},
            complete=not next_token and index + 1 >= len(selection.resource_ids),
        )

    async def incremental_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        marker = state.get("history_id")
        if not marker:
            return await self.initial_sync(selection, state)
        params: dict[str, str | int] = {"startHistoryId": string(marker), "maxResults": self.config.page_size}
        if state.get("page_token"):
            params["pageToken"] = string(state["page_token"])
        try:
            result = await self.get("/history", params)
        except ConnectorError as error:
            if error.code == "not_found":
                return (await self.initial_sync(selection, {})).model_copy(update={"reconcile": True})
            raise
        changed: dict[str, Resource] = {}
        deleted: set[str] = set()
        for entry in records(result.get("history", [])):
            for change_type in ("messagesAdded", "labelsAdded", "labelsRemoved", "messagesDeleted"):
                for item in records(entry.get(change_type, [])):
                    message = record(item["message"])
                    identifier = string(message["id"])
                    if change_type == "messagesDeleted":
                        deleted.add(identifier)
                    else:
                        changed[identifier] = Resource(
                            id=identifier, name=identifier, kind="email_message", parent_id=string(message["threadId"])
                        )
        token = result.get("nextPageToken")
        return SyncPage(
            resources=[ref for identifier, ref in changed.items() if identifier not in deleted],
            deleted_ids=sorted(deleted),
            next_state={"history_id": marker, "page_token": token or ""},
            checkpoint={"history_id": string(result.get("historyId", marker))},
            complete=not token,
        )

    async def fetch_resource(self, resource: Resource, selection: Selection) -> FetchResult:
        if resource.kind == "email_attachment":
            message_id = string(resource.metadata["message_id"])
            attachment_id = resource.metadata.get("attachment_id")
            encoded_attachment = quote(string(attachment_id), safe="") if attachment_id else ""
            data = (
                unbase64(
                    (await self.get(f"/messages/{quote(message_id, safe='')}/attachments/{encoded_attachment}"))["data"]
                )
                if attachment_id
                else unbase64(resource.metadata["data"])
            )
            safe_resource = resource.model_copy(
                update={"metadata": {key: value for key, value in resource.metadata.items() if key != "data"}}
            )
            envelope = await self.binary(
                safe_resource, data, string(resource.metadata["filename"]), string(resource.metadata["mime_type"])
            )
            return FetchResult(envelopes=[envelope])
        message = await self.get("/messages/" + quote(resource.id, safe=""), {"format": "full"})
        labels = strings(message.get("labelIds", []))
        received = datetime.fromtimestamp(number(message["internalDate"]) / 1000, UTC)
        if (
            not set(labels) & set(selection.resource_ids)
            or (selection.start_date and received.date() < selection.start_date)
            or (selection.end_date and received.date() > selection.end_date)
        ):
            return FetchResult(removed_ids=[resource.id])
        payload = record(message["payload"])
        headers = {string(h["name"]).casefold(): string(h["value"]) for h in records(payload.get("headers", []))}
        mail = EmailMessage()
        for name in ("from", "to", "cc", "bcc", "subject", "date", "message-id", "in-reply-to", "references"):
            if name in headers:
                mail[name] = headers[name]
        plain: list[str] = []
        html: list[str] = []
        followups: list[Resource] = []
        pending = [(payload, 0)]
        version = string(message["historyId"])
        while pending:
            part, depth = pending.pop(0)
            if depth > 20 or len(followups) > 100:
                raise ConnectorError("too_large", "Email MIME structure exceeds processing limits")
            pending.extend((child, depth + 1) for child in records(part.get("parts", [])))
            body = record(part.get("body", {}))
            filename = string(part.get("filename", ""))
            mime = string(part.get("mimeType", "application/octet-stream"))
            if filename and selection.attachments:
                part_id = string(part.get("partId", str(len(followups))))
                followups.append(
                    Resource(
                        id=f"{resource.id}:attachment:{part_id}",
                        name=filename,
                        kind="email_attachment",
                        parent_id=resource.id,
                        version=version,
                        metadata={
                            "message_id": resource.id,
                            "thread_id": message["threadId"],
                            "attachment_id": body.get("attachmentId"),
                            "data": body.get("data", ""),
                            "filename": filename,
                            "mime_type": mime,
                        },
                    )
                )
            elif not filename and body.get("data") and mime in {"text/plain", "text/html"}:
                try:
                    part_headers = {
                        string(h["name"]).casefold(): string(h["value"]) for h in records(part.get("headers", []))
                    }
                    part_mail = EmailMessage()
                    part_mail["Content-Type"] = part_headers.get("content-type", mime)
                    text = unbase64(body["data"]).decode(part_mail.get_content_charset() or "utf-8")
                except (UnicodeError, LookupError) as error:
                    raise ConnectorError(
                        "unsupported_encoding", "Email body character encoding could not be decoded"
                    ) from error
                (html if mime == "text/html" else plain).append(text)
        if plain:
            mail.set_content("\n\n".join(plain))
        elif html:
            mail.set_content("\n\n".join(html), subtype="html")
        else:
            mail.set_content("")
        if plain and html:
            mail.add_alternative("\n\n".join(html), subtype="html")
            mail.set_boundary("fixflow-" + hashlib.sha256((resource.id + ":" + version).encode()).hexdigest()[:40])
        metadata: dict[str, object] = {
            "message_id": resource.id,
            "thread_id": string(message["threadId"]),
            "labels": labels,
            "received_at": received.isoformat(),
            "created_at": received.isoformat(),
            **{name: headers.get(name, "") for name in ("from", "to", "cc", "bcc", "subject", "date")},
        }
        ref = resource.model_copy(
            update={"version": version, "parent_id": string(message["threadId"]), "metadata": metadata}
        )
        return FetchResult(
            envelopes=[self.factory.text(ref, mail.as_string(), "message.eml", "message/rfc822")], followups=followups
        )

    async def handle_event(self, event: dict[str, object]) -> list[Resource]:
        # Push is a wake-up hint. The authoritative history endpoint supplies changes/deletions.
        return []

    async def structured_query(self, query: QuerySpec, selection: Selection) -> QueryResult:
        labels = [query.resource_id] if query.resource_id else selection.resource_ids
        if len(labels) != 1 or labels[0] not in selection.resource_ids:
            raise ConnectorError("invalid_query", "Choose one selected Gmail label for a structured query")
        filters = query.selected_window(selection)
        if filters is None:
            return QueryResult(resources=[], count=0, exact=True)
        terms = [self.date_query(filters)]
        for field, value in (("from", query.sender), ("to", query.recipient)):
            if value:
                if any(c in value for c in '\r\n\x00"() ') or "@" not in value:
                    raise ConnectorError("invalid_query", "Use an exact email address for sender/recipient filters")
                terms.append(field + ":" + value)
        params: dict[str, str | int] = {"labelIds": labels[0], "q": " ".join(terms), "maxResults": query.limit}
        if query.cursor:
            params["pageToken"] = query.cursor
        result = await self.get("/messages", params)
        refs = [
            Resource(id=string(m["id"]), name=string(m["id"]), kind="email_message", parent_id=string(m["threadId"]))
            for m in records(result.get("messages", []))
        ]
        cursor = result.get("nextPageToken")
        return QueryResult(
            resources=refs if query.operation == "list" else [],
            count=len(refs),
            exact=not cursor,
            next_cursor=string(cursor) if cursor else None,
        )

    async def health_check(self) -> bool:
        await self.get("/profile")
        return True
