"""Durable, bounded Grok function loop with server-enforced workspace/action authority."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from asyncpg import PostgresError
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError, number, record, records, string
from backend.connectors.rates import reserve
from backend.db.models import AgentAction, AgentFile, AgentRun, ConnectorAccount
from backend.db.session import get_session_factory
from backend.schemas.agent import (
    DraftEmail,
    Finish,
    Material,
    PrepareFile,
    ReadProvider,
    RunRequest,
    RunView,
    SearchProvider,
    SearchSnapshots,
)
from backend.schemas.connectors import Selection
from backend.schemas.pipeline import digest
from backend.security.policy import POLICY_VERSION, PolicyViolation, redact, serialized_size, validate_disclosure
from backend.security.prompt import SYSTEM_PROMPT
from backend.security.runtime import configured_secrets, request_policy
from backend.services import agent_actions, agent_files, grok, proofos_tools
from backend.services.access import owner_id
from backend.services.workspace_tools import (
    ACTIVE,
    TOOL_MODELS,
    Context,
    ReadSource,
    authorize,
    connection,
    ensure_mutable,
    material,
    native_read,
    read_canonical,
    snapshots,
    tool_definitions,
)

logger = logging.getLogger(__name__)
INSTRUCTIONS = SYSTEM_PROMPT


def context(run: AgentRun) -> Context:
    return Context(
        run.owner_id,
        run.id,
        RunRequest.model_validate(run.request),
        dict(run.scope),
        [Material.model_validate(value) for value in run.materials],
    )


async def owned(db: AsyncSession, identifier: UUID) -> AgentRun:
    run = await db.scalar(
        select(AgentRun)
        .where(AgentRun.id == identifier, AgentRun.owner_id == owner_id(db))
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise HTTPException(404, "Workflow not found")
    return run


async def ancestors(db: AsyncSession, latest: AgentRun, *, limit: int = 8) -> list[AgentRun]:
    """Bounded conversation context; every ancestor is resolved through owner isolation."""
    messages = [latest]
    seen = {latest.id}
    while len(messages) < limit:
        identifier = messages[-1].request.get("parent_run_id")
        if not identifier:
            break
        parent_id = UUID(string(identifier))
        if parent_id in seen:
            raise HTTPException(409, "Conversation context is invalid; start a new chat")
        seen.add(parent_id)
        messages.append(await owned(db, parent_id))
    return list(reversed(messages))


async def create(db: AsyncSession, request: RunRequest) -> AgentRun:
    config = get_settings().grok
    await reserve("agent-run:" + owner_id(db), 60, config.requests_per_minute)
    try:
        request_policy(request.question)
        validate_disclosure(request.model_dump(mode="json"), configured_secrets())
    except PolicyViolation as error:
        raise HTTPException(403, str(error)) from error
    if not config.configured or config.model is None:
        raise HTTPException(503, "Set backend GROK__API_KEY and GROK__MODEL to enable chat")
    accounts = list(
        await db.scalars(
            select(ConnectorAccount).where(
                ConnectorAccount.owner_id == owner_id(db),
                ConnectorAccount.status.in_(ACTIVE),
                ConnectorAccount.authentication_status == "valid",
                ConnectorAccount.credential_reference.is_not(None),
            )
        )
    )
    if not request.include_connections:
        accounts = []
    if request.account_ids:
        if not set(request.account_ids) <= {account.id for account in accounts}:
            raise HTTPException(404, "An authorized connection was not found")
        accounts = [account for account in accounts if account.id in request.account_ids]
    if len(accounts) > 20:
        raise HTTPException(422, "Select at most 20 connections for this workflow")
    scope = {
        "policy_version": POLICY_VERSION,
        "accounts": [
            {
                "id": str(account.id),
                "provider": account.provider,
                "name": account.display_name,
                "selection_hash": digest(account.configuration),
                "selected_resources": Selection.model_validate(account.configuration).resource_ids[:20],
                "selected_count": len(Selection.model_validate(account.configuration).resource_ids),
            }
            for account in accounts
        ]
    }
    messages: list[dict[str, object]] = [{"role": "system", "content": INSTRUCTIONS}]
    materials: list[dict[str, object]] = []
    if request.parent_run_id:
        parent = await owned(db, request.parent_run_id)
        try:
            request_policy(string(parent.request["question"]))
        except PolicyViolation as error:
            raise HTTPException(403, "Previous request does not pass current policy; start a new request") from error
        await authorize(context(parent))
        if parent.status != "completed":
            raise HTTPException(409, "Wait for the previous workflow to finish before following up")
        parent_materials = [Material.model_validate(value) for value in parent.materials]
        allowed = {str(account.id) for account in accounts}
        if any(
            value.reference.get("account_id") and value.reference["account_id"] not in allowed
            for value in parent_materials
        ):
            raise HTTPException(409, "Follow-up scope excludes a previous source; start a new request")
        for previous in await ancestors(db, parent):
            try:
                request_policy(string(previous.request["question"]))
                validate_disclosure(previous.answer, configured_secrets())
            except PolicyViolation as error:
                raise HTTPException(403, "Previous content does not pass current policy; start a new chat") from error
            await authorize(context(previous))
            messages += [
                {"role": "user", "content": string(previous.request["question"])},
                {"role": "assistant", "content": json.dumps(previous.answer, ensure_ascii=False)},
            ]
        materials = list(parent.materials)
        try:
            validate_disclosure([materials, parent.answer], configured_secrets())
        except PolicyViolation as error:
            raise HTTPException(403, "Previous workflow contains restricted content; start a new request") from error
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": request.question,
                    "project": request.project,
                    "connections": scope["accounts"],
                    "action_mode": request.action_mode,
                    "email_enabled": request.email_enabled,
                    "authorized_recipients": request.allowed_recipients,
                    "model_step_limit": config.max_steps,
                    "prior_evidence": materials,
                },
                ensure_ascii=False,
            ),
        }
    )
    # Serialize per-owner capacity checks without holding this lock across model/provider HTTP.
    key = int.from_bytes(hashlib.sha256(("agent-capacity:" + owner_id(db)).encode()).digest()[:8], "big", signed=True)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise HTTPException(429, "Workflow admission is busy; retry shortly", headers={"Retry-After": "2"})
    active = await db.scalar(
        select(func.count()).select_from(AgentRun).where(
            AgentRun.owner_id == owner_id(db), AgentRun.status.in_(("queued", "running"))
        )
    )
    if (active or 0) >= get_settings().security.active_runs_per_owner:
        raise HTTPException(429, "Active workflow limit reached; wait or cancel a run", headers={"Retry-After": "5"})
    run = AgentRun(
        owner_id=owner_id(db),
        request=request.model_dump(mode="json"),
        scope=scope,
        model=config.model,
        messages=messages,
        materials=materials,
        usage={
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "embedding_calls": 0,
            "embedding_input_tokens": 0,
        },
    )
    db.add(run)
    await db.commit()
    return run


async def view(db: AsyncSession, run: AgentRun) -> RunView:
    visible_question = string(run.request["question"])
    try:
        validate_disclosure(visible_question, configured_secrets())
    except PolicyViolation:
        visible_question = "Restricted workflow question"
    try:
        validate_disclosure([run.request, run.materials, run.answer], configured_secrets())
        await authorize(context(run))
    except (ConnectorError, ValueError):
        return RunView(
            id=run.id,
            question=visible_question,
            status="cancelled",
            answer=None,
            materials=[],
            actions=[],
            files=[],
            steps=run.steps,
            usage=run.usage,
            error="Workspace access or selection changed; private results are unavailable",
            created_at=run.created_at,
            parent_run_id=RunRequest.model_validate(run.request).parent_run_id,
        )
    actions = list(
        await db.scalars(
            select(AgentAction)
            .where(AgentAction.run_id == run.id, AgentAction.owner_id == run.owner_id)
            .order_by(AgentAction.created_at)
        )
    )
    action_views = []
    for action in actions:
        try:
            validate_disclosure(action.payload, configured_secrets())
            action_views.append(agent_actions.view(action))
        except PolicyViolation:
            action_views.append(agent_actions.view(action).model_copy(update={
                "payload": {}, "status": "blocked", "error": "Draft contains restricted content"
            }))
    files = list(
        await db.scalars(
            select(AgentFile)
            .where(AgentFile.run_id == run.id, AgentFile.owner_id == run.owner_id)
            .order_by(AgentFile.created_at)
        )
    )
    return RunView(
        id=run.id,
        question=visible_question,
        status=run.status,
        answer=Finish.model_validate(run.answer) if run.answer else None,
        materials=context(run).materials,
        actions=action_views,
        files=[agent_files.view(file) for file in files],
        steps=run.steps,
        usage=run.usage,
        error=run.error,
        created_at=run.created_at,
        parent_run_id=RunRequest.model_validate(run.request).parent_run_id,
    )


async def cancel(db: AsyncSession, run: AgentRun) -> None:
    run.status, run.run_token, run.pending_calls, run.error = "cancelled", None, [], "Cancelled by the owner"
    await db.commit()


async def dispatch(ctx: Context, call: dict[str, object]) -> dict[str, object]:
    name = string(call["name"])
    if name not in TOOL_MODELS:
        raise ValueError("Unknown workspace tool")
    if name == "draft_email" and not ctx.request.email_enabled:
        raise PolicyViolation("Email actions were not authorized for this workflow")
    args = TOOL_MODELS[name][0].model_validate_json(string(call["arguments"]))
    validate_disclosure(args.model_dump(mode="json"), configured_secrets())
    if name in proofos_tools.PROOF_TOOL_MODELS:
        return await proofos_tools.dispatch(ctx, name, args, material)
    if call.get("waiting_source_id"):
        return await read_canonical(
            ctx,
            ReadSource(source_id=UUID(string(call["waiting_source_id"]))),
            record(call.get("waiting_reference", {})),
        )
    if isinstance(args, SearchSnapshots):
        return await snapshots(ctx, args)
    if isinstance(args, SearchProvider):
        workspace, _ = await connection(ctx, args.account_id)
        result = await workspace.search(args)
        entry = material(
            json.dumps(result, ensure_ascii=False),
            "Native " + workspace.connector.provider + " discovery",
            {
                "account_id": str(args.account_id),
                "provider": workspace.connector.provider,
                "operation": "search",
                "search_request": args.model_dump(mode="json"),
                "resource_ids": [value.get("id") for value in records(result.get("resources", []))],
                "query": args.query,
                "scope": "bounded selected resources",
            },
        )
        return {**result, "materials": [entry.model_dump(mode="json")]}
    if isinstance(args, PrepareFile):
        return await agent_files.prepare(ctx, args)
    if isinstance(args, ReadSource):
        return await read_canonical(ctx, args)
    if isinstance(args, ReadProvider):
        if name == "github_status":
            workspace, _ = await connection(ctx, args.account_id)
            result = await workspace.github_status(args)
            entry = material(
                json.dumps(result, ensure_ascii=False),
                "GitHub observed repository status",
                {
                    "account_id": str(args.account_id),
                    "provider": "github",
                    "resource_id": args.resource_id,
                    "operation": "status",
                    "status_request": args.model_dump(mode="json"),
                    "observed": datetime.now(UTC).isoformat(),
                },
            )
            return {"materials": [entry.model_dump(mode="json")], "facts": result}
        return await native_read(ctx, args)
    if isinstance(args, DraftEmail):
        return await agent_actions.draft(ctx, args)
    if isinstance(args, Finish):
        validate_disclosure(args.model_dump(mode="json"), configured_secrets())
        await authorize(ctx, native=True)
        known = {value.id: value.text for value in ctx.materials}
        for claim in args.claims:
            for citation in claim.citations:
                if (
                    citation.evidence_id not in known
                    or not citation.quote.strip()
                    or citation.quote not in known[citation.evidence_id]
                ):
                    raise ValueError("Final answer includes fabricated evidence")
        # Keep factual answer text structurally linked to verified claims.
        return {"finished": args.model_dump(mode="json")}
    raise ValueError("Unsupported tool arguments")


async def process_step(identifier: UUID) -> None:
    config = get_settings().grok
    token = uuid4()
    now = datetime.now(UTC)
    async with get_session_factory().begin() as db:
        run = await db.scalar(select(AgentRun).where(AgentRun.id == identifier).with_for_update(skip_locked=True))
        if run is None or run.status not in {"queued", "running"} or run.next_attempt_at > now:
            return
        if run.status == "running" and run.lease_until and run.lease_until > now:
            return
        if not config.configured or run.model != config.model:
            run.status, run.error = "failed", "Grok model/key configuration is missing or changed"
            return
        if now - run.created_at > timedelta(seconds=config.run_timeout_seconds):
            run.status, run.error = "failed", "Workflow deadline exceeded; narrow the task or retry explicitly"
            return
        ctx = context(run)
        try:
            if run.scope.get("policy_version") != POLICY_VERSION or run.messages[0] != {
                "role": "system", "content": SYSTEM_PROMPT
            }:
                raise PolicyViolation("Workflow policy changed; start a new workflow")
            request_policy(ctx.request.question)
        except (PolicyViolation, IndexError):
            run.status, run.run_token, run.error = "failed", None, "Workflow request no longer passes current policy"
            return
        try:
            await authorize(ctx)
        except ConnectorError:
            run.status, run.run_token, run.error = "cancelled", None, "Workspace access changed"
            return
        call = dict(run.pending_calls[0]) if run.pending_calls else None
        if call is None and run.steps >= config.max_steps:
            run.status, run.error = (
                "failed",
                "Workflow model-step budget exhausted; collected evidence remains available",
            )
            return
        if call is None:
            run.steps += 1
            run.usage = {**run.usage, "model_calls": (run.usage.get("model_calls") or 0) + 1}
        run.status, run.run_token = "running", token
        run.lease_until = now + timedelta(seconds=max(config.timeout_seconds, 180) + 30)
        messages = list(run.messages)
    result: dict[str, object] = {}
    response: grok.GrokStep | None = None
    failure: str | None = None
    cancelled = False
    delay = 0.0
    try:
        if call is None:
            try:
                await reserve("grok-call:" + ctx.owner, 60, config.requests_per_minute)
            except ConnectorError as error:
                delay = max(1, error.retry_after or 60)
                raise
            await authorize(ctx)
            await ensure_mutable(ctx, automatic=True)
            response = await grok.respond(messages, tool_definitions(ctx.request.email_enabled), config)
        else:
            await authorize(ctx)
            async with asyncio.timeout(180):
                result = await dispatch(ctx, call)
            serialized_size(result)
            cleaned = record(redact(result, configured_secrets()))
            originals = records(result.get("materials", []))
            for original, sanitized in zip(originals, records(cleaned.get("materials", [])), strict=True):
                if original.get("text") != sanitized.get("text"):
                    sanitized["warning"] = (
                        "Credential-like text was redacted; canonical source spans retain original offsets"
                    )
            result = cleaned
    except PolicyViolation as error:
        failure = str(error)
    except ConnectorError as error:
        if error.code in {"inaccessible", "reauth_required", "credential_unavailable"}:
            failure, cancelled = "Workspace access changed; workflow stopped", True
        elif delay:
            failure = "Grok rate limit; workflow will resume"
        else:
            # Return bounded tool errors to the model so it can explain missing capabilities or choose another tool.
            if call is not None:
                result = {"error": error.code, "message": str(error), "retry_after": error.retry_after}
            else:
                failure = "Grok request could not be completed; no automatic paid retry"
    except HTTPException as error:
        if call is not None:
            result = {"error": "tool_unavailable", "message": str(error.detail)}
        else:
            failure = "Workflow request could not be completed"
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {401, 403}:
            failure = (
                "Chat provider denied access. Check the xAI API key, credits, spending limit and model permissions."
            )
        elif error.response.status_code == 429:
            failure = "Chat provider rate limit reached. Try again later."
        else:
            failure = "Chat provider is unavailable. Try again later; no automatic paid retry."
    except (
        httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError
    ):
        failure = "Workflow validation or Grok request failed; no automatic paid retry"
    async with get_session_factory().begin() as db:
        run = await db.scalar(select(AgentRun).where(AgentRun.id == identifier).with_for_update())
        if run is None or run.run_token != token or run.status != "running":
            return
        run.run_token, run.lease_until = None, None
        if failure:
            run.status, run.error = ("cancelled" if cancelled else "queued" if delay else "failed"), failure
            if call is None and not delay:
                run.usage = {**run.usage, "input_tokens": None, "output_tokens": None}
            run.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            if delay and call is None:
                run.steps -= 1
                run.usage = {**run.usage, "model_calls": max(0, (run.usage.get("model_calls") or 0) - 1)}
            return
        try:
            await authorize(context(run))
        except ConnectorError:
            run.status, run.error = "cancelled", "Workspace access changed; results discarded"
            return
        if response:
            usage = dict(run.usage)
            for key, count in [("input_tokens", response.input_tokens), ("output_tokens", response.output_tokens)]:
                usage[key] = usage.get(key, 0) + count if usage.get(key, 0) is not None and count is not None else None
            run.usage = usage
            previous_ids = {item.get("call_id") for item in run.messages if item.get("type") == "function_call"}
            allowed = {value["name"] for value in tool_definitions(ctx.request.email_enabled)}
            if any(item["call_id"] in previous_ids or item["name"] not in allowed for item in response.calls):
                run.status, run.error = "failed", "Unknown or duplicate model tool call rejected"
                return
            run.messages = [*run.messages, *response.output]
            run.pending_calls = response.calls
        elif call:
            if result.get("pending_source_id"):
                waiting = {
                    **call,
                    "waiting_source_id": result["pending_source_id"],
                    "waiting_reference": result.get("pending_reference", {}),
                }
                run.pending_calls = [waiting, *run.pending_calls[1:]]
                run.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2)
            else:
                additions = [Material.model_validate(value) for value in records(result.get("materials", []))]
                values = {value["id"]: value for value in run.materials}
                values.update({value.id: value.model_dump(mode="json") for value in additions})
                if len(values) > 32:
                    run.status, run.error = "failed", "Evidence budget reached; narrow the request"
                    return
                run.materials = list(values.values())
                if "embedding_calls" in result:
                    usage = dict(run.usage)
                    usage["embedding_calls"] = (usage.get("embedding_calls") or 0) + number(result["embedding_calls"])
                    embedding_count = result.get("embedding_input_tokens")
                    if number(result["embedding_calls"]):
                        previous = usage.get("embedding_input_tokens")
                        usage["embedding_input_tokens"] = (
                            previous + number(embedding_count)
                            if previous is not None and embedding_count is not None
                            else None
                        )
                    run.usage = usage
                run.messages = [
                    *run.messages,
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(result, ensure_ascii=False),
                    },
                ]
                run.pending_calls = run.pending_calls[1:]
                if result.get("finished"):
                    run.answer, run.status, run.error = record(result["finished"]), "completed", None
                    return
        run.status, run.error = "queued", None


async def worker() -> None:
    while True:
        try:
            async with get_session_factory()() as db:
                ids = list(
                    await db.scalars(
                        select(AgentRun.id)
                        .where(
                            AgentRun.status.in_(("queued", "running")),
                            AgentRun.next_attempt_at <= func.clock_timestamp(),
                            or_(AgentRun.lease_until.is_(None), AgentRun.lease_until <= func.clock_timestamp()),
                        )
                        .order_by(AgentRun.next_attempt_at)
                        .limit(2)
                    )
                )
            for identifier in ids:
                try:
                    await process_step(identifier)
                except ConnectorError:
                    async with get_session_factory().begin() as db:
                        run = await db.get(AgentRun, identifier)
                        if run and run.status in {"queued", "running"}:
                            run.status, run.run_token, run.error = "cancelled", None, "Workspace access changed"
        except (SQLAlchemyError, PostgresError, OSError, ValueError, TimeoutError) as error:
            logger.error("Workspace workflow queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(1)
