"""GitHub App access is restricted to repositories authorized by both the user and installation."""

import json
import mimetypes
import re
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import quote

import jwt

from backend.connectors.base import OAuthConnector
from backend.connectors.core import ConnectorError, number, record, records, string, unbase64
from backend.processing.pipeline.inspection import CODE_LANGUAGES, EXTENSIONS
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


class GitHubConnector(OAuthConnector):
    provider: Provider = "github"
    authorization_endpoint = "https://github.com/login/oauth/authorize"
    exchange_endpoint = "https://github.com/login/oauth/access_token"
    api_root = "https://api.github.com"

    def api_headers(self) -> dict[str, str]:
        return {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": self.config.github_api_version}

    async def array(
        self, path: str, params: dict[str, str | int] | None = None, *, token: str | None = None
    ) -> list[dict[str, object]]:
        result = records(
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

    async def handle_callback(self, code: str, verifier: str) -> tuple[Credentials, str, str, dict[str, object]]:
        credentials = await self.exchange(code, verifier)
        user = await self.get("/user", token=credentials.access_token.get_secret_value())
        return credentials, str(number(user["id"])), string(user["login"]), {"login": string(user["login"])}

    async def list_resources(self, cursor: str | None = None) -> ResourcePage:
        state = record(json.loads(cursor)) if cursor else {}
        installation_page = number(state.get("installation_page", 1))
        installation_ids = state.get("installations")
        total = number(state.get("total", 0))
        if installation_ids is None:
            result = await self.get("/user/installations", {"per_page": 100, "page": installation_page})
            installation_ids = [
                number(item["id"])
                for item in records(result.get("installations", []))
                if str(item.get("app_id")) == self.config.github_app_id
            ]
            total = number(result.get("total_count", 0))
        if not isinstance(installation_ids, list):
            raise ConnectorError("invalid_resource", "Invalid repository discovery cursor")
        index = number(state.get("index", 0))
        if index >= len(installation_ids):
            next_cursor = (
                json.dumps({"installation_page": installation_page + 1}) if installation_page * 100 < total else None
            )
            return ResourcePage(resources=[], next_cursor=next_cursor)
        installation = number(installation_ids[index])
        page = number(state.get("repository_page", 1))
        result = await self.get(
            f"/user/installations/{installation}/repositories", {"per_page": self.config.page_size, "page": page}
        )
        refs = [
            Resource(
                id=str(number(repo["id"])),
                name=string(repo["full_name"]),
                kind="repository",
                metadata={
                    "full_name": string(repo["full_name"]),
                    "installation_id": installation,
                    "default_branch": string(repo["default_branch"]),
                    "private": repo.get("private", True),
                },
            )
            for repo in records(result.get("repositories", []))
        ]
        more = page * self.config.page_size < number(result.get("total_count", 0))
        next_state = {
            "installations": installation_ids,
            "index": index if more else index + 1,
            "repository_page": page + 1 if more else 1,
            "installation_page": installation_page,
            "total": total,
        }
        complete = not more and index + 1 >= len(installation_ids) and installation_page * 100 >= total
        return ResourcePage(resources=refs, next_cursor=None if complete else json.dumps(next_state))

    async def repository(self, identifier: str) -> dict[str, object]:
        if not identifier.isdigit():
            raise ConnectorError("invalid_resource", "Choose an authorized repository ID")
        repo = await self.get("/repositories/" + identifier)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", string(repo["full_name"])):
            raise ConnectorError("invalid_response", "Provider returned an invalid repository name")
        return repo

    def app_jwt(self) -> str:
        if self.config.github_private_key is None or self.config.github_app_id is None:
            raise ConnectorError("not_configured", "GitHub App signing is not configured")
        now = int(time.time())
        try:
            return jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": self.config.github_app_id},
                self.config.github_private_key.get_secret_value().replace("\\n", "\n"),
                algorithm="RS256",
            )
        except (ValueError, jwt.PyJWTError) as error:
            raise ConnectorError("not_configured", "GitHub App signing key is invalid") from error

    async def installation_token(self, repo: dict[str, object], selection: Selection) -> str:
        # The user-token repository probe has already enforced the user's current access.
        installation = await self.get("/repos/" + string(repo["full_name"]) + "/installation", token=self.app_jwt())
        if str(installation.get("app_id")) != self.config.github_app_id:
            raise ConnectorError("inaccessible", "Repository is not installed for this GitHub App")
        permissions: dict[str, object] = {"metadata": "read"}
        if set(selection.categories) & {"code", "commits", "releases"}:
            permissions["contents"] = "read"
        if "issues" in selection.categories:
            permissions["issues"] = "read"
        if "pull_requests" in selection.categories:
            permissions["pull_requests"] = "read"
        result = record(
            await self.http.request(
                "POST",
                f"{self.api_root}/app/installations/{number(installation['id'])}/access_tokens",
                token=self.app_jwt(),
                payload={"repository_ids": [number(repo["id"])], "permissions": permissions},
                headers=self.api_headers(),
            )
        )
        return string(result["token"])

    async def initial_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        index = number(state.get("index", 0))
        heads = record(state.get("heads", {}))
        since = state.get("since")
        started = state.get("started") or datetime.now(UTC).isoformat()
        checkpoint = {"since": started, "heads": heads}
        if index >= len(selection.resource_ids):
            return SyncPage(complete=True, checkpoint=checkpoint)
        repo_id = selection.resource_ids[index]
        repo = await self.repository(repo_id)
        name = string(repo["full_name"])
        branch = selection.branches.get(repo_id, string(repo["default_branch"]))
        branch_result = await self.get(f"/repos/{name}/branches/{quote(branch, safe='')}")
        head = string(heads.get(repo_id) or record(branch_result["commit"])["sha"])
        categories = ["repository", *selection.categories]
        stage = number(state.get("stage", 0))
        category = categories[stage]
        page = number(state.get("page", 1))
        metadata: dict[str, object] = {
            "repository_id": repo_id,
            "repository": name,
            "branch": branch,
            "commit_sha": head,
        }
        refs: list[Resource] = []
        cache: list[dict[str, object]] = records(state.get("tree_cache", []))
        more = False
        if category == "repository":
            refs = [
                Resource(
                    id=f"{repo_id}:repository",
                    name=name,
                    kind="repository_metadata",
                    parent_id=repo_id,
                    version=digest([repo.get("description"), repo.get("updated_at"), branch, head]),
                    metadata={
                        **metadata,
                        "object": {
                            key: repo.get(key)
                            for key in (
                                "full_name",
                                "description",
                                "html_url",
                                "default_branch",
                                "private",
                                "updated_at",
                            )
                        },
                    },
                )
            ]
        elif category == "code":
            previous_heads = record(state.get("previous_heads", {}))
            if since and previous_heads.get(repo_id) == head:
                cache = []
            else:
                if not cache and page == 1:
                    tree = await self.get(f"/repos/{name}/git/trees/{quote(head, safe='')}", {"recursive": "1"})
                    if tree.get("truncated") is True:
                        raise ConnectorError(
                            "too_large",
                            "Repository tree is truncated; select a smaller repository or add a tree adapter",
                        )
                    cache = [item for item in records(tree.get("tree", [])) if item.get("type") == "blob"]
                    if len(cache) > self.config.max_sync_resources:
                        raise ConnectorError("too_large", "Repository exceeds the configured resource limit")
                offset = (page - 1) * self.config.page_size
                for item in cache[offset : offset + self.config.page_size]:
                    path = string(item["path"])
                    refs.append(
                        Resource(
                            id=f"{repo_id}:file:{branch}:{path}",
                            name=path,
                            kind="source_file",
                            parent_id=repo_id,
                            version=string(item["sha"]),
                            metadata={
                                **metadata,
                                "file_path": path,
                                "blob_sha": string(item["sha"]),
                                "sync_group": f"repository:{repo_id}:code:{branch}",
                                "language": CODE_LANGUAGES.get(PurePosixPath(path).suffix.lower()),
                                "file_role": "readme"
                                if PurePosixPath(path).name.lower().startswith("readme")
                                else "documentation"
                                if PurePosixPath(path).suffix.lower() in {".md", ".rst", ".txt"}
                                else "source",
                            },
                        )
                    )
                more = page * self.config.page_size < len(cache)
        else:
            endpoint = {"issues": "issues", "pull_requests": "pulls", "commits": "commits", "releases": "releases"}[
                category
            ]
            params: dict[str, str | int] = {"per_page": self.config.page_size, "page": page}
            if category in {"issues", "pull_requests"}:
                params.update(state="all", sort="updated", direction="desc")
            if category == "commits":
                params["sha"] = head
            if since and category in {"issues", "commits"}:
                params["since"] = string(since)
            items = await self.array(f"/repos/{name}/{endpoint}", params)
            more = len(items) == self.config.page_size
            for item in items:
                if category == "issues" and "pull_request" in item:
                    continue
                updated = item.get("updated_at")
                if since and category == "pull_requests" and updated and string(updated) <= string(since):
                    more = False
                    break
                identifier = (
                    string(item["sha"])
                    if category == "commits"
                    else str(number(item["number"] if category in {"issues", "pull_requests"} else item["id"]))
                )
                allowed = {
                    key: item.get(key)
                    for key in (
                        "id",
                        "number",
                        "title",
                        "name",
                        "body",
                        "state",
                        "html_url",
                        "created_at",
                        "updated_at",
                        "tag_name",
                        "published_at",
                        "sha",
                        "commit",
                    )
                    if key in item
                }
                kind = {"issues": "issue", "pull_requests": "pull_request", "commits": "commit", "releases": "release"}[
                    category
                ]
                refs.append(
                    Resource(
                        id=f"{repo_id}:{kind}:{identifier}",
                        name=str(item.get("title") or item.get("name") or identifier),
                        kind=kind,
                        parent_id=repo_id,
                        version=string(updated) if updated else digest(allowed),
                        metadata={**metadata, "object": allowed, "number": identifier},
                    )
                )
        heads = {**heads, repo_id: head}
        next_stage = stage if more else stage + 1
        next_index = index + int(next_stage >= len(categories))
        next_state = {
            "index": next_index,
            "stage": 0 if next_stage >= len(categories) else next_stage,
            "page": page + 1 if more else 1,
            "started": started,
            "since": since,
            "heads": heads,
            "previous_heads": state.get("previous_heads", {}),
            "tree_cache": cache if more and category == "code" else [],
        }
        return SyncPage(
            resources=refs,
            next_state=next_state,
            checkpoint={"since": started, "heads": heads},
            complete=next_index >= len(selection.resource_ids),
            completed_inventory_groups=[f"repository:{repo_id}:code:{branch}"]
            if category == "code"
            and not more
            and (not since or record(state.get("previous_heads", {})).get(repo_id) != head)
            else [],
        )

    async def incremental_sync(self, selection: Selection, state: dict[str, object]) -> SyncPage:
        if "previous_heads" not in state:
            state = {**state, "previous_heads": state.get("heads", {}), "heads": {}}
        return await self.initial_sync(selection, state)

    async def fetch_resource(self, resource: Resource, selection: Selection) -> FetchResult:
        repository_id = string(resource.metadata["repository_id"])
        if repository_id not in selection.resource_ids:
            raise ConnectorError("inaccessible", "Repository was not selected")
        repo = await self.repository(repository_id)
        name = string(repo["full_name"])
        if resource.kind.startswith("event_"):
            kind = resource.kind.removeprefix("event_")
            identifier = string(resource.metadata["number"])
            endpoints = {"issue": "issues", "pull_request": "pulls", "release": "releases"}
            if kind not in endpoints or not identifier.isdigit():
                raise ConnectorError("invalid_resource", "Invalid GitHub event resource")
            item = await self.get(f"/repos/{name}/{endpoints[kind]}/{identifier}")
            allowed = {
                key: item.get(key)
                for key in (
                    "id",
                    "number",
                    "title",
                    "name",
                    "body",
                    "state",
                    "html_url",
                    "created_at",
                    "updated_at",
                    "tag_name",
                    "published_at",
                )
                if key in item
            }
            authoritative = resource.model_copy(
                update={
                    "id": f"{repository_id}:{kind}:{identifier}",
                    "kind": kind,
                    "name": str(item.get("title") or item.get("name") or identifier),
                    "version": str(item.get("updated_at") or digest(allowed)),
                    "metadata": {**resource.metadata, "object": allowed},
                }
            )
            return await self.fetch_resource(authoritative, selection)
        if resource.kind == "source_file":
            token = await self.installation_token(repo, selection)
            blob = await self.get(
                f"/repos/{name}/git/blobs/{quote(string(resource.metadata['blob_sha']), safe='')}", token=token
            )
            data = unbase64(string(blob["content"]).replace("\n", ""))
            path = PurePosixPath(string(resource.metadata["file_path"]))
            filename = path.name
            if path.suffix.lower() not in EXTENSIONS:
                try:
                    content = data.decode("utf-8")
                except UnicodeError as error:
                    raise ConnectorError(
                        "unsupported", "Repository file is not a supported document or UTF-8 source"
                    ) from error
                return FetchResult(
                    envelopes=[self.factory.text(resource, content, filename[:245] + ".txt", "text/plain")]
                )
            return FetchResult(
                envelopes=[
                    await self.binary(resource, data, filename, mimetypes.guess_type(filename)[0] or "text/plain")
                ]
            )
        if resource.kind.startswith("page_"):
            return await self.fetch_page(resource, name, selection)
        item = record(resource.metadata.get("object", {}))
        body = string(item.get("body", "")) if item.get("body") is not None else ""
        facts = {key: value for key, value in item.items() if key not in {"body", "commit"} and value is not None}
        if "commit" in item:
            commit = record(item["commit"])
            body = string(commit.get("message", ""))
            facts["parents"] = item.get("parents", [])
        raw = (
            "# "
            + resource.name
            + "\n\n"
            + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in facts.items())
            + "\n\n"
            + body
        )
        envelope = self.factory.text(resource, raw, resource.kind + ".md", "text/markdown")
        followups: list[Resource] = []
        number_id = string(resource.metadata.get("number", ""))
        if resource.kind == "issue":
            followups.append(self.page_ref(resource, "issue_comments", number_id))
        elif resource.kind == "pull_request":
            followups.extend(
                self.page_ref(resource, category, number_id)
                for category in ("issue_comments", "reviews", "review_comments", "changed_files")
            )
        elif resource.kind == "commit":
            details = await self.get(f"/repos/{name}/commits/{quote(number_id, safe='')}")
            envelope.metadata["changed_files"] = [file.get("filename") for file in records(details.get("files", []))]
        return FetchResult(envelopes=[envelope], followups=followups)

    @staticmethod
    def page_ref(parent: Resource, category: str, number_id: str, page: int = 1) -> Resource:
        return Resource(
            id=f"{parent.id}:page:{category}:{page}",
            name=category,
            kind="page_" + category,
            parent_id=parent.id,
            metadata={**parent.metadata, "number": number_id, "page": page, "parent_resource": parent.id},
        )

    async def fetch_page(self, resource: Resource, name: str, selection: Selection) -> FetchResult:
        category = resource.kind.removeprefix("page_")
        identifier = string(resource.metadata["number"])
        page = number(resource.metadata["page"])
        routes = {
            "issue_comments": f"issues/{identifier}/comments",
            "reviews": f"pulls/{identifier}/reviews",
            "review_comments": f"pulls/{identifier}/comments",
            "changed_files": f"pulls/{identifier}/files",
        }
        if category not in routes or not identifier.isdigit():
            raise ConnectorError("invalid_resource", "Invalid GitHub relationship resource")
        items = await self.array(f"/repos/{name}/{routes[category]}", {"per_page": self.config.page_size, "page": page})
        envelopes = []
        for item in items:
            item_id = str(item.get("id") or item.get("filename"))
            allowed: dict[str, object] = {
                key: item.get(key)
                for key in (
                    "body",
                    "state",
                    "path",
                    "filename",
                    "status",
                    "html_url",
                    "created_at",
                    "updated_at",
                    "submitted_at",
                    "commit_id",
                    "pull_request_review_id",
                    "in_reply_to_id",
                )
                if key in item
            }
            if "user" in item:
                allowed["author_id"] = record(item["user"]).get("id")
            kind = {
                "issue_comments": "issue_comment",
                "reviews": "review",
                "review_comments": "review_comment",
                "changed_files": "changed_file",
            }[category]
            ref = Resource(
                id=f"{resource.metadata['repository_id']}:{kind}:{resource.metadata['number']}:{item_id}",
                name=kind + " " + item_id,
                kind=kind,
                parent_id=string(resource.metadata["parent_resource"]),
                version=str(item.get("updated_at") or item.get("submitted_at") or digest(allowed)),
                metadata={
                    "repository_id": resource.metadata["repository_id"],
                    "repository": name,
                    "object": allowed,
                    "related_resource": resource.metadata["parent_resource"],
                    "sync_group": "relationships:" + string(resource.metadata["parent_resource"]) + ":" + category,
                },
            )
            body = str(allowed.get("body") or "")
            raw = (
                "# "
                + ref.name
                + "\n\n"
                + "\n".join(
                    f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in allowed.items() if key != "body"
                )
                + "\n\n"
                + body
            )
            envelopes.append(self.factory.text(ref, raw, kind + ".md", "text/markdown"))
        followups = (
            [
                resource.model_copy(
                    update={
                        "id": resource.id.rsplit(":", 1)[0] + f":{page + 1}",
                        "metadata": {**resource.metadata, "page": page + 1},
                    }
                )
            ]
            if len(items) == self.config.page_size
            else []
        )
        return FetchResult(
            envelopes=envelopes,
            followups=followups,
            completed_inventory_groups=[]
            if followups
            else ["relationships:" + string(resource.metadata["parent_resource"]) + ":" + category],
        )

    async def handle_event(self, event: dict[str, object]) -> list[Resource]:
        repository_id = str(record(event.get("repository", {})).get("id", ""))
        if not self.account or repository_id not in Selection.model_validate(self.account.configuration).resource_ids:
            return []
        for key, kind in (("pull_request", "pull_request"), ("issue", "issue"), ("release", "release")):
            item = event.get(key)
            if isinstance(item, dict):
                identifier = str(item.get("id") if kind == "release" else item.get("number"))
                if identifier.isdigit():
                    return [
                        Resource(
                            id=f"{repository_id}:event:{kind}:{identifier}",
                            name="Changed " + kind,
                            kind="event_" + kind,
                            parent_id=repository_id,
                            metadata={"repository_id": repository_id, "number": identifier},
                        )
                    ]
        return []

    async def structured_query(self, query: QuerySpec, selection: Selection) -> QueryResult:
        if query.sender or query.recipient or query.start_date or query.end_date:
            raise ConnectorError("invalid_query", "GitHub structured queries support selected repository metadata only")
        if query.cursor and (not query.cursor.isdigit() or int(query.cursor) > len(selection.resource_ids)):
            raise ConnectorError("invalid_query", "Invalid repository query cursor")
        offset = int(query.cursor or "0")
        identifiers = (
            [query.resource_id] if query.resource_id else selection.resource_ids[offset : offset + query.limit]
        )
        if any(identifier not in selection.resource_ids for identifier in identifiers):
            raise ConnectorError("inaccessible", "Query references an unselected repository")
        refs = [
            Resource(id=identifier, name=string((await self.repository(identifier))["full_name"]), kind="repository")
            for identifier in identifiers
        ]
        complete = query.resource_id is not None or offset + query.limit >= len(selection.resource_ids)
        return QueryResult(
            resources=refs if query.operation == "list" else [],
            count=len(refs),
            exact=complete,
            next_cursor=None if complete else str(offset + query.limit),
        )

    async def health_check(self) -> bool:
        await self.get("/user")
        return True
