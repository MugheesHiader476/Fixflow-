"""Immutable email outbox. Ambiguous delivery is never retried automatically."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from backend.connectors.core import ConnectorError
from backend.db.models import AgentAction
from backend.db.session import get_session_factory
from backend.schemas.agent import ActionView, DraftEmail
from backend.schemas.pipeline import digest
from backend.security.policy import POLICY_VERSION, PolicyViolation, outbound_email
from backend.security.rates import email_dispatch
from backend.security.runtime import configured_secrets, request_policy
from backend.services.workspace_tools import Context, authorize, connection, ensure_mutable, lock_authority


def view(action: AgentAction) -> ActionView:
    return ActionView(
        id=action.id,
        account_id=action.account_id,
        status=action.status,
        payload=action.payload,
        payload_hash=action.payload_hash,
        provider_message_id=action.provider_message_id,
        error=action.error,
    )


async def draft(ctx: Context, args: DraftEmail) -> dict[str, object]:
    if not ctx.request.email_enabled:
        raise PolicyViolation("Email actions were not authorized for this workflow")
    outbound_email(args.model_dump(mode="json"), configured_secrets())
    await ensure_mutable(ctx, automatic=True)
    known = {item.id for item in ctx.materials}
    if known and not args.evidence_ids:
        raise ConnectorError("invalid_query", "Link the draft to the evidence IDs used to compose it")
    if not set(args.evidence_ids) <= known:
        raise ConnectorError("invalid_query", "Email draft references unknown workspace evidence")
    await authorize(ctx, native=True)
    workspace, _ = await connection(ctx, args.account_id)
    payload = await workspace.email_payload(args)
    fingerprint = digest(payload)
    intent = digest({key: value for key, value in payload.items() if key not in {"original_version", "evidence_ids"}})
    async with get_session_factory().begin() as db:
        await lock_authority(db, ctx, args.account_id)
        await db.execute(
            insert(AgentAction)
            .values(
                id=uuid4(),
                owner_id=ctx.owner,
                run_id=ctx.run_id,
                account_id=args.account_id,
                payload=payload,
                payload_hash=fingerprint,
                intent_hash=intent,
            )
            .on_conflict_do_nothing()
        )
        action = await db.scalar(
            select(AgentAction).where(
                AgentAction.run_id == ctx.run_id,
                AgentAction.owner_id == ctx.owner,
                AgentAction.intent_hash == intent,
            )
        )
        if action is None:
            raise HTTPException(409, "The email draft could not be persisted")
        identifier = action.id
        fingerprint = action.payload_hash
    if ctx.request.action_mode == "auto" and args.recipient in ctx.request.allowed_recipients:
        await execute(ctx, identifier, fingerprint, automatic=True)
    async with get_session_factory()() as db:
        action = await db.get(AgentAction, identifier)
        if action is None:
            raise HTTPException(404, "The email draft no longer exists")
        return {
            "action_id": str(action.id),
            "status": action.status,
            "recipient": args.recipient,
            "subject": action.payload.get("subject"),
            "provider_message_id": action.provider_message_id,
            "error": action.error,
            "authorization": "automatic recipient policy"
            if ctx.request.action_mode == "auto" and args.recipient in ctx.request.allowed_recipients
            else "review and execute this exact draft",
        }


async def execute(ctx: Context, identifier: UUID, expected_hash: str, *, automatic: bool = False) -> ActionView:
    if not ctx.request.email_enabled or ctx.scope.get("policy_version") != POLICY_VERSION:
        raise HTTPException(403, "Start a new workflow with explicit email permission")
    try:
        request_policy(ctx.request.question)
    except PolicyViolation as error:
        raise HTTPException(403, str(error)) from error
    # Recheck all consulted evidence before authorizing an outbound action.
    await authorize(ctx, native=True)
    await ensure_mutable(ctx, automatic=automatic)
    async with get_session_factory().begin() as db:
        key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
        if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
            raise HTTPException(409, "Action is being processed")
        action = await db.scalar(
            select(AgentAction)
            .where(AgentAction.id == identifier, AgentAction.run_id == ctx.run_id, AgentAction.owner_id == ctx.owner)
            .with_for_update()
        )
        if action is None:
            raise HTTPException(404, "Action not found")
        if action.payload_hash != expected_hash or digest(action.payload) != expected_hash:
            raise HTTPException(409, "Draft content changed; refresh and review it again")
        if action.status == "sent":
            return view(action)
        if action.status in {"dispatching", "unknown"}:
            action.status, action.error = (
                "unknown",
                "Delivery is uncertain; check Gmail before preparing any new message",
            )
            return view(action)
        account_id, payload = action.account_id, dict(action.payload)
        try:
            outbound_email(payload, configured_secrets())
        except PolicyViolation as error:
            action.status, action.error = "blocked", str(error)
            return view(action).model_copy(update={"payload": {}})
        action.status, action.error = "dispatching", None
    try:
        workspace, _ = await connection(ctx, account_id)
        await authorize(ctx, native=True)

        async def before_send() -> None:
            try:
                outbound_email(payload, configured_secrets())
            except PolicyViolation as error:
                raise ConnectorError("policy_violation", str(error)) from error
            await ensure_mutable(ctx, automatic=automatic)
            await authorize(ctx)
            await email_dispatch(ctx.owner)

        message_id = await workspace.send_email(payload, identifier, before_send=before_send)
    except ConnectorError as error:
        async with get_session_factory().begin() as db:
            action = await db.get(AgentAction, identifier, with_for_update=True)
            if action is None:
                raise HTTPException(404, "Action no longer exists") from error
            action.status = "unknown" if error.code == "delivery_unknown" else "blocked"
            action.error = str(error)
            result = view(action)
        await authorize(ctx)
        return result
    async with get_session_factory().begin() as db:
        action = await db.get(AgentAction, identifier, with_for_update=True)
        if action is None:
            raise HTTPException(404, "Action no longer exists")
        action.status, action.provider_message_id, action.error = "sent", message_id, None
        result = view(action)
    await authorize(ctx)
    return result
