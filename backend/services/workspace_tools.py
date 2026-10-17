"""Owner-scoped tool dispatch, current evidence and canonical document reads."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import cast
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError, records, string
from backend.connectors.drive import GoogleDriveConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.gmail import GmailConnector
from backend.connectors.registry import adapter
from backend.connectors.slack import SlackConnector
from backend.connectors.workspace import NativeWorkspace
from backend.db.models import AgentRun, ConnectorAccount, Document, KnowledgeSource, PassageIndex
from backend.db.session import get_session_factory
from backend.repositories.parsed import parsed_source
from backend.repositories.source_access import accessible_source
from backend.schemas.agent import (
    DraftEmail,
    Finish,
    Material,
    PrepareFile,
    ReadProvider,
    RunRequest,
    SearchProvider,
    SearchSnapshots,
)
from backend.schemas.connectors import Provider, Selection
from backend.schemas.evidence import EvidenceRequest
from backend.schemas.pipeline import Contract, digest
from backend.services import evidence
from backend.services.connector_sources import asset_path, discard_unreferenced, register, reusable_file
from backend.services.ingestion import IngestionError, ingest_source

ACTIVE = ("connected", "syncing", "connected_with_warning")


class ReadSource(Contract):
    source_id: UUID
    block_offset: int = Field(default=0, ge=0, le=100000)
    character_offset: int = Field(default=0, ge=0, le=5000000)


TOOL_MODELS: dict[str, tuple[type[BaseModel], str]] = {
    "search_snapshots": (
        SearchSnapshots,
        "Search authorized parsed uploads and connector snapshots with lexical/hybrid retrieval. Query "
        "keywords, not claims of live freshness. Optional project scopes indexed snapshots.",
    ),
    "search_provider": (
        SearchProvider,
        "Discover current selected Drive files, Gmail messages (Gmail search syntax), GitHub "
        "repositories or Slack channel messages (supply a selected channel resource_id). Paginated; "
        "use returned cursors and IDs. No arbitrary URLs.",
    ),
    "read_resource": (
        ReadProvider,
        "Fetch a selected native Drive/Gmail/GitHub resource through canonical parsing. GitHub "
        "resource_id is repository numeric ID; path is repository-relative. Returns source_id for "
        "paginated read_source.",
    ),
    "read_source": (
        ReadSource,
        "Read another exact canonical source span by source_id and block/character offsets. Returns "
        "pagination for long documents.",
    ),
    "github_status": (
        ReadProvider,
        "Read observed selected GitHub branch commits, issues, pull requests and releases. Bounded "
        "pages, not exhaustive counts or a guarantee of project completion.",
    ),
    "prepare_file": (
        PrepareFile,
        "Prepare a private downloadable file ticket from Drive or GitHub. Google Workspace documents "
        "export as PDF; other files must already be PDF for format=pdf.",
    ),
    "draft_email": (
        DraftEmail,
        "Prepare an email or threaded reply. For replies recipient must equal verified original From; "
        "provide reply_message_id. Automatic sending is controlled only by user-approved "
        "recipients/mode. Cite evidence_ids used in the draft.",
    ),
    "finish": (
        Finish,
        "Finish with answer, exact-quote grounded claims and limitations. Cite only returned material "
        "IDs and exact text. Explain partial/missing data and actual action statuses; never claim "
        "delivery without a sent receipt.",
    ),
}


@lru_cache(maxsize=2)
def tool_definitions(email_enabled: bool = False) -> list[dict[str, object]]:
    return [
        {"type": "function", "name": name, "description": description, "parameters": model.model_json_schema()}
        for name, (model, description) in TOOL_MODELS.items()
        if email_enabled or name != "draft_email"
    ]


@dataclass(frozen=True)
class Context:
    owner: str
    run_id: UUID
    request: RunRequest
    scope: dict[str, object]
    materials: list[Material]

    def accounts(self) -> list[dict[str, object]]:
        return records(self.scope.get("accounts", []))


async def connection(ctx: Context, identifier: UUID) -> tuple[NativeWorkspace, ConnectorAccount]:
    captured = next((item for item in ctx.accounts() if item.get("id") == str(identifier)), None)
    if captured is None:
        raise ConnectorError("inaccessible", "Connection was not authorized for this workflow")
    async with get_session_factory()() as db:
        account = await db.scalar(
            select(ConnectorAccount)
            .where(
                ConnectorAccount.id == identifier,
                ConnectorAccount.owner_id == ctx.owner,
                ConnectorAccount.status.in_(ACTIVE),
                ConnectorAccount.authentication_status == "valid",
                ConnectorAccount.credential_reference.is_not(None),
            )
            .execution_options(populate_existing=True)
        )
        if account is None or digest(account.configuration) != captured.get("selection_hash"):
            raise ConnectorError("inaccessible", "Connection access or resource selection changed")
        selection = Selection.model_validate(account.configuration)
    candidate = adapter(cast(Provider, account.provider), get_settings().connectors, account)
    if not isinstance(candidate, (GmailConnector, GoogleDriveConnector, GitHubConnector, SlackConnector)):
        raise ConnectorError("unsupported", "Unknown native connector")
    return NativeWorkspace(candidate, selection), account


async def authorize(ctx: Context, *, native: bool = False) -> None:
    for captured in ctx.accounts():
        await connection(ctx, UUID(string(captured["id"])))
    async with get_session_factory()() as db:
        for material in ctx.materials:
            ref = material.reference
            if ref.get("source_id"):
                allowed_ids = [UUID(string(item["id"])) for item in ctx.accounts()]
                scoped = await db.scalar(
                    select(KnowledgeSource.id).where(
                        KnowledgeSource.id == UUID(string(ref["source_id"])),
                        KnowledgeSource.owner_id == ctx.owner,
                        accessible_source(current_time=True),
                        or_(
                            KnowledgeSource.connector_account_id.is_(None),
                            KnowledgeSource.connector_account_id.in_(allowed_ids),
                        ),
                    )
                )
                if scoped is None:
                    raise ConnectorError("inaccessible", "Evidence is outside this workflow's authorized connections")
                result = await parsed_source(db, UUID(string(ref["source_id"])), ctx.owner)
                if result is None or result.canonical.source.sha256 != ref.get("source_hash"):
                    raise ConnectorError("inaccessible", "Source access or version changed")
                if ref.get("canonical_hash") and result.canonical.content_hash != ref["canonical_hash"]:
                    raise ConnectorError("processing", "Canonical evidence changed; run the workflow again")
            if native and ref.get("account_id") and ref.get("read_request"):
                workspace, _ = await connection(ctx, UUID(string(ref["account_id"])))
                resource = await workspace.resource(ReadProvider.model_validate(ref["read_request"]))
                if resource.version != ref.get("version"):
                    raise ConnectorError("processing", "Live source changed; run the workflow again")
            if native and ref.get("account_id"):
                workspace, _ = await connection(ctx, UUID(string(ref["account_id"])))
                await workspace.check_reference(ref)


async def ensure_mutable(ctx: Context, *, automatic: bool = False) -> None:
    async with get_session_factory()() as db:
        status = await db.scalar(
            select(AgentRun.status).where(AgentRun.id == ctx.run_id, AgentRun.owner_id == ctx.owner)
        )
    if status is None or status in {"cancelled", "failed"} or (automatic and status not in {"queued", "running"}):
        raise ConnectorError("inaccessible", "Workflow stopped; no further action is authorized")


async def lock_authority(db: AsyncSession, ctx: Context, identifier: UUID) -> ConnectorAccount:
    """Fence writes against disconnect/purge in their account-then-run lock order."""
    account = await db.scalar(
        select(ConnectorAccount)
        .where(ConnectorAccount.id == identifier, ConnectorAccount.owner_id == ctx.owner)
        .with_for_update()
    )
    captured = next((item for item in ctx.accounts() if item.get("id") == str(identifier)), None)
    if (
        account is None
        or captured is None
        or account.status not in ACTIVE
        or account.authentication_status != "valid"
        or account.credential_reference is None
        or digest(account.configuration) != captured.get("selection_hash")
    ):
        raise ConnectorError("inaccessible", "Connection access changed before persistence")
    run = await db.get(AgentRun, ctx.run_id, with_for_update=True)
    if run is None or run.owner_id != ctx.owner or run.status not in {"queued", "running"}:
        raise ConnectorError("inaccessible", "Workflow stopped before persistence")
    return account


def material(text: str, title: str, reference: dict[str, object], *, warning: str | None = None) -> Material:
    if len(text) > 8000:
        text, warning = text[:8000], "This is a bounded excerpt; additional content is not included."
    return Material(
        id="e-" + digest([reference, text])[:32],
        title=title[:500],
        text=text,
        reference=reference,
        observed_at=datetime.now(UTC),
        warning=warning,
    )


async def read_canonical(
    ctx: Context,
    args: ReadSource,
    reference: dict[str, object] | None = None,
    *,
    match_terms: list[str] | None = None,
) -> dict[str, object]:
    async with get_session_factory()() as db:
        source = await db.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.id == args.source_id,
                KnowledgeSource.owner_id == ctx.owner,
                accessible_source(current_time=True),
            )
        )
        allowed = {string(item["id"]) for item in ctx.accounts()}
        if source is None or (source.connector_account_id and str(source.connector_account_id) not in allowed):
            raise ConnectorError("inaccessible", "Source is outside the authorized workspace")
        if source.status in {"uploaded", "processing", "chunked"}:
            return {"pending_source_id": str(source.id), "pending_reference": reference or {}}
        if source.status == "failed":
            raise ConnectorError("parse_failed", source.error_message or "Document parsing failed; needs review")
        result = await parsed_source(db, args.source_id, ctx.owner)
        if result is None:
            raise ConnectorError("inaccessible", "A current parsed source is unavailable")
    blocks = result.canonical.blocks
    if match_terms and blocks:
        pattern = re.compile("|".join(re.escape(term) for term in match_terms), re.IGNORECASE)
        matches = [pattern.search(block.content) for block in blocks]
        best = max(
            range(len(blocks)),
            key=lambda index: sum(term.casefold() in blocks[index].content.casefold() for term in match_terms),
        )
        first = matches[best]
        args = ReadSource(
            source_id=args.source_id,
            block_offset=best,
            character_offset=max(0, first.start() - 200) if first else 0,
        )
    if args.block_offset >= len(blocks):
        return {"materials": [], "next_block": None}
    index, offset, budget = args.block_offset, args.character_offset, 7800
    pieces: list[str] = []
    spans: list[dict[str, object]] = []
    while index < len(blocks) and len(pieces) < 5 and budget > 0:
        block = blocks[index]
        if offset > len(block.content):
            raise ConnectorError("invalid_query", "Character offset is outside the canonical block")
        end = min(len(block.content), offset + budget)
        piece = block.content[offset:end]
        if piece:
            pieces.append(piece)
            spans.append(
                {
                    "block_id": block.block_id,
                    "start": offset,
                    "end": end,
                    "provenance": block.provenance.model_dump(mode="json"),
                }
            )
            budget -= len(piece) + 2
        if end < len(block.content):
            offset = end
            break
        index, offset = index + 1, 0
    ref = {
        **(reference or {}),
        "source_id": str(args.source_id),
        "source_hash": result.canonical.source.sha256,
        "canonical_hash": result.canonical.content_hash,
        "source_version": result.canonical.source.version,
        "spans": spans,
    }
    entry = material("\n\n".join(pieces), result.canonical.source.filename, ref)
    return {
        "materials": [entry.model_dump(mode="json")],
        "source_id": str(args.source_id),
        "next_block": index if index < len(blocks) else None,
        "next_character": offset,
    }


async def snapshots(ctx: Context, args: SearchSnapshots) -> dict[str, object]:
    captured = [item for item in ctx.accounts() if args.provider is None or item.get("provider") == args.provider]
    ids = [UUID(string(item["id"])) for item in captured]
    manual = args.provider is None
    request = EvidenceRequest(
        question=args.query,
        project=ctx.request.project or "workspace",
        all_projects=ctx.request.project is None,
        connector_account_ids=ids,
        include_manual_sources=manual,
        max_passages=5,
        context_tokens=2200,
        retrieval="hybrid" if get_settings().gemini.configured else "lexical",
    )
    output: list[Material] = []
    async with get_session_factory()() as db:
        db.info["owner_id"] = ctx.owner
        result = await evidence.search(db, request, get_settings().gemini)
        for hit in result.passages:
            p = hit.passage
            output.append(
                material(
                    p.text,
                    str(p.metadata.get("source", "Source")),
                    {
                        "source_id": p.source_id,
                        "source_hash": p.source_hash,
                        "canonical_hash": p.canonical_hash,
                        "passage_id": p.id,
                        "spans": [span.model_dump(mode="json") for span in p.spans],
                        "lexical_rank": hit.lexical_rank,
                        "vector_rank": hit.vector_rank,
                    },
                )
            )
        # Include parsed documents that have no opt-in passage index, while keeping SQL ACL first.
        match_terms = list(dict.fromkeys(re.findall(r"\w+", args.query)[:24]))
        terms = " OR ".join(match_terms)
        if terms:
            query = func.websearch_to_tsquery("english", terms)
            indexed = select(PassageIndex.source_id)
            statement = (
                select(KnowledgeSource.id)
                .join(Document, Document.source_id == KnowledgeSource.id)
                .where(
                    KnowledgeSource.owner_id == ctx.owner,
                    KnowledgeSource.status == "parsed",
                    accessible_source(current_time=True),
                    KnowledgeSource.id.not_in(indexed),
                    or_(
                        KnowledgeSource.connector_account_id.in_(ids),
                        KnowledgeSource.connector_account_id.is_(None) if manual else false(),
                    ),
                    func.to_tsvector("english", Document.page_content).op("@@")(query),
                )
                .order_by(func.ts_rank_cd(func.to_tsvector("english", Document.page_content), query).desc())
                .limit(5)
            )
            # Unlabelled snapshots have no trustworthy project membership.
            sources = list(await db.scalars(statement)) if ctx.request.project is None else []
        else:
            sources = []
    for identifier in sources:
        page = await read_canonical(ctx, ReadSource(source_id=identifier), match_terms=match_terms)
        output.extend(Material.model_validate(item) for item in records(page.get("materials", [])))
    return {
        "materials": [item.model_dump(mode="json") for item in output[:8]],
        "warnings": result.warnings,
        "embedding_calls": result.embedding_calls,
        "embedding_input_tokens": result.embedding_input_tokens,
        "coverage": "bounded authorized indexed passages and unindexed parsed snapshots; not a live scan",
    }


async def native_read(ctx: Context, args: ReadProvider) -> dict[str, object]:
    workspace, account = await connection(ctx, args.account_id)
    resource = await workspace.resource(args)
    async with get_session_factory()() as db:
        cached = await reusable_file(db, account, resource)
    reference: dict[str, object] = {
        "resource_id": args.resource_id,
        "path": args.path,
        "revision": args.revision,
        "version": resource.version,
        "provider": account.provider,
    }
    if cached is not None:
        envelope = cached
    else:
        fetched, reference = await workspace.fetch(args)
        if not fetched.envelopes:
            raise ConnectorError("inaccessible", "Selected native resource is unavailable")
        envelope = fetched.envelopes[0]
    assets = [asset_path(envelope.binary_asset_reference)] if envelope.binary_asset_reference else []
    reference.update(account_id=str(account.id), read_request=args.model_dump(mode="json"))
    try:
        async with get_session_factory().begin() as db:
            current = await lock_authority(db, ctx, args.account_id)
            await register(db, current, envelope, assets)
        try:
            await ingest_source(envelope.source_id)
        except IngestionError as error:
            raise ConnectorError("parse_failed", str(error)) from error
        return await read_canonical(ctx, ReadSource(source_id=envelope.source_id), reference)
    finally:
        await discard_unreferenced([path for path in assets if path.exists()])
