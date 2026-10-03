"""Small durable sync steps: a page or a resource, with restart-safe cursors and leases."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

from asyncpg import PostgresError
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.connectors.core import Connector, ConnectorError, record, records, string, strings
from backend.connectors.github import GitHubConnector
from backend.connectors.registry import adapter, configured, verify_selection
from backend.db.models import ConnectorAccount, ConnectorResource, ConnectorSyncJob, KnowledgeSource
from backend.db.session import get_session_factory
from backend.schemas.connectors import FetchResult, Provider, Resource, Selection
from backend.services.connector_sources import asset_path, discard_unreferenced, register, remove, reusable_file
from backend.services.connectors import ACTIVE, PENDING, audit, enqueue, failure

logger = logging.getLogger(__name__)


def drain_events(account: ConnectorAccount, cursor: dict[str, object]) -> None:
    arrivals = records(account.meta.get("pending_events", []))
    if arrivals:
        pending = records(cursor.get("pending", []))
        identifiers = {item.get("id") for item in pending}
        cursor["pending"] = [*pending, *(item for item in arrivals if item.get("id") not in identifiers)]
        account.meta = {k: v for k, v in account.meta.items() if k != "pending_events"}


async def verify_job_selection(connector: Connector, account: ConnectorAccount, cursor: dict[str, object]) -> Selection:
    requested = Selection.model_validate(cursor.get("requested_selection", cursor["selection"]))
    try:
        await verify_selection(connector, requested)
        lost: list[str] = []
        valid = requested.resource_ids
    except ConnectorError as error:
        if error.code not in {"inaccessible", "not_found"}:
            raise
        valid, lost = [], []
        for identifier in requested.resource_ids:
            single = requested.model_copy(
                update={
                    "resource_ids": [identifier],
                    "branches": {identifier: requested.branches[identifier]}
                    if identifier in requested.branches
                    else {},
                }
            )
            try:
                await verify_selection(connector, single)
                valid.append(identifier)
            except ConnectorError as root_error:
                if root_error.code not in {"inaccessible", "not_found"}:
                    raise
                lost.append(identifier)
    selection = requested.model_copy(
        update={
            "resource_ids": valid,
            "branches": {key: value for key, value in requested.branches.items() if key in valid},
        }
    )
    previous_lost = strings(cursor.get("unavailable_selections", []))
    if lost != previous_lost:
        # Start a full inventory of the remaining authorized roots. Disabled
        # sources are re-enabled only by an authoritative envelope, never by a lease.
        async with get_session_factory().begin() as db:
            live = await db.get(ConnectorAccount, account.id, with_for_update=True)
            if live and live.status in ACTIVE:
                await db.execute(
                    update(KnowledgeSource)
                    .where(KnowledgeSource.connector_account_id == account.id)
                    .values(is_active=False, access_expires_at=datetime.now(UTC))
                )
        cursor.update(state={}, pending=[], complete=False, inventory_groups=[], checkpoint={}, repair_selection=True)
    cursor["requested_selection"] = requested.model_dump(mode="json")
    cursor["selection"] = selection.model_dump(mode="json")
    cursor["unavailable_selections"] = lost
    return selection


async def process_job(identifier: UUID) -> bool:
    """An advisory transaction prevents concurrent steps even if a slow request outlives a lease."""
    async with get_session_factory().begin() as guard:
        key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
        if not await guard.scalar(select(func.pg_try_advisory_xact_lock(key))):
            return False
        async with get_session_factory().begin() as db:
            job = await db.get(ConnectorSyncJob, identifier, with_for_update=True)
            if job is None or job.status not in PENDING or job.available_at > datetime.now(UTC):
                return False
            account = await db.get(ConnectorAccount, job.account_id)
            if account is None or account.status not in ACTIVE:
                job.status = "cancelled"
                return False
            job.status, job.lease_until = "running", datetime.now(UTC) + timedelta(minutes=5)
            account.sync_status = "running"
            if not job.cursor.get("verified"):
                audit(db, account.owner_id, "sync_started", account.id)
            cursor, progress = dict(job.cursor), dict(job.progress)
            mode, started = job.mode, job.created_at
        ref = None
        assets: list[Path] = []
        try:
            connector = adapter(cast(Provider, account.provider), get_settings().connectors, account)
            selection = Selection.model_validate(cursor["selection"])
            pending = records(cursor.get("pending", []))
            verified_at = cursor.get("verified_at")
            if (
                not cursor.get("verified")
                or not verified_at
                or datetime.fromisoformat(string(verified_at))
                + timedelta(seconds=get_settings().connectors.acl_ttl_seconds / 2)
                <= datetime.now(UTC)
            ):
                selection = await verify_job_selection(connector, account, cursor)
                pending = records(cursor.get("pending", []))
                if cursor.pop("repair_selection", False):
                    mode = "reconcile"
                    async with get_session_factory().begin() as db:
                        live_job = await db.get(ConnectorSyncJob, identifier)
                        if live_job and live_job.status in PENDING:
                            live_job.mode = "reconcile"
                await connector.health_check()
                if isinstance(connector, GitHubConnector) and connector.observed_installations:
                    async with get_session_factory().begin() as db:
                        live = await db.get(ConnectorAccount, account.id, with_for_update=True)
                        if live and live.status in ACTIVE:
                            live.meta = {**live.meta, "installations": connector.observed_installations}
                cursor["verified"] = True
                cursor["verified_at"] = datetime.now(UTC).isoformat()
                from backend.connectors.subscriptions import ensure_subscription  # noqa: PLC0415

                try:
                    await ensure_subscription(connector, account)
                except ConnectorError as error:
                    if error.code == "reauth_required":
                        raise
                    cursor["notification_warning"] = True
                    audit(guard, account.owner_id, "notification_setup_failed", account.id, code=error.code)
            ref = Resource.model_validate(pending[0]) if pending else None
            if ref:
                async with get_session_factory()() as db:
                    cached = await reusable_file(db, account, ref)
                fetched = FetchResult(envelopes=[cached]) if cached else await connector.fetch_resource(ref, selection)
                assets.extend(
                    asset_path(envelope.binary_asset_reference)
                    for envelope in fetched.envelopes
                    if envelope.binary_asset_reference
                )
                if progress["discovered"] + len(fetched.followups) > get_settings().connectors.max_sync_resources:
                    raise ConnectorError("too_large", "Selected resources exceed the configured synchronization limit")
                progress["discovered"] += len(fetched.followups)
                async with get_session_factory().begin() as db:
                    current = await db.get(ConnectorAccount, account.id, with_for_update=True)
                    live_job = await db.get(ConnectorSyncJob, identifier, with_for_update=True)
                    if (
                        current is None
                        or live_job is None
                        or current.status not in ACTIVE
                        or live_job.status not in PENDING
                    ):
                        return False
                    for envelope in fetched.envelopes:
                        changed = await register(db, current, envelope, assets)
                        field = "queued" if changed else "unchanged"
                        progress[field] = progress.get(field, 0) + 1
                        audit(
                            db,
                            current.owner_id,
                            "resource_queued" if changed else "resource_unchanged",
                            account.id,
                            source_id=str(envelope.source_id),
                        )
                    progress["removed"] += await remove(db, account.id, fetched.removed_ids)
                    followups = [item.model_dump(mode="json") for item in fetched.followups]
                    cursor["pending"] = [*followups, *pending[1:]]
                    cursor["inventory_groups"] = list(
                        dict.fromkeys(
                            [*strings(cursor.get("inventory_groups", [])), *fetched.completed_inventory_groups]
                        )
                    )
                    drain_events(current, cursor)
                    cursor.pop("retries", None)
                    live_job.cursor, live_job.progress = cursor, progress
                    live_job.status, live_job.lease_until = "pending", None
                    live_job.available_at = datetime.now(UTC)
                return True
            if cursor.get("complete"):
                # Long scans may outlive a lease. Check the selected roots again
                # before renewing unchanged content or committing a high-water mark.
                await verify_job_selection(connector, account, cursor)
                if cursor.pop("repair_selection", False):
                    async with get_session_factory().begin() as db:
                        live_job = await db.get(ConnectorSyncJob, identifier)
                        if live_job and live_job.status in PENDING:
                            live_job.cursor, live_job.mode, live_job.status = cursor, "reconcile", "pending"
                    return True
                await finish(identifier, account.id, cursor, progress, mode, started)
                return True
            state = record(cursor.get("state", {}))
            page = await (
                connector.incremental_sync(selection, state)
                if mode == "incremental"
                else connector.initial_sync(selection, state)
            )
            progress["discovered"] += len(page.resources)
            if progress["discovered"] > get_settings().connectors.max_sync_resources:
                raise ConnectorError("too_large", "Selected resources exceed the configured synchronization limit")
            cursor.update(
                state=page.next_state,
                pending=[item.model_dump(mode="json") for item in page.resources],
                checkpoint=page.checkpoint,
                complete=page.complete,
                inventory_groups=list(
                    dict.fromkeys([*strings(cursor.get("inventory_groups", [])), *page.completed_inventory_groups])
                ),
            )
            async with get_session_factory().begin() as db:
                current = await db.get(ConnectorAccount, account.id, with_for_update=True)
                live_job = await db.get(ConnectorSyncJob, identifier, with_for_update=True)
                if (
                    current is None
                    or live_job is None
                    or current.status not in ACTIVE
                    or live_job.status not in PENDING
                ):
                    return False
                if page.reconcile:
                    live_job.mode = "reconcile"
                    audit(db, current.owner_id, "reconciliation_repair", account.id)
                progress["removed"] += await remove(db, account.id, page.deleted_ids)
                drain_events(current, cursor)
                live_job.cursor, live_job.progress = cursor, progress
                live_job.status, live_job.lease_until = "pending", None
                live_job.available_at = datetime.now(UTC)
            return True
        except (HTTPException, ValidationError, ValueError, KeyError, TypeError) as error:
            safe = ConnectorError("invalid_resource", "Resource could not be normalized or its format is unsupported")
            logger.warning("Connector normalization failed account=%s type=%s", account.id, type(error).__name__)
            await step_failure(identifier, account.id, safe, cursor, progress, ref)
        except ConnectorError as error:
            await step_failure(identifier, account.id, error, cursor, progress, ref)
        finally:
            await discard_unreferenced(assets)
        return False


async def step_failure(
    identifier: UUID,
    account_id: UUID,
    error: ConnectorError,
    cursor: dict[str, object],
    progress: dict[str, int],
    ref: Resource | None,
) -> None:
    await failure(account_id, error)
    async with get_session_factory().begin() as db:
        job = await db.get(ConnectorSyncJob, identifier, with_for_update=True)
        account = await db.get(ConnectorAccount, account_id, with_for_update=True)
        if job is None or account is None or job.status not in PENDING or account.status not in ACTIVE:
            return
        retries = int(str(cursor.get("retries", 0)))
        if ref is None and error.code == "inaccessible":
            await db.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.connector_account_id == account_id)
                .values(is_active=False)
            )
            account.status, account.sync_status, job.status = "connected_with_warning", "failed", "failed"
            job.error_message, job.lease_until = str(error), None
            audit(db, account.owner_id, "selection_access_lost", account_id)
            return
        if error.code in {"rate_limited", "processing"}:
            job.status = "waiting"
            job.available_at = datetime.now(UTC) + timedelta(seconds=max(1, error.retry_after))
            audit(db, account.owner_id, "rate_limit" if error.code == "rate_limited" else "source_busy", account_id)
        elif ref and (
            error.code in {"not_found", "inaccessible", "unsupported", "invalid_resource", "too_large"}
            or retries >= get_settings().connectors.max_retries
        ):
            if error.code in {"not_found", "inaccessible"}:
                progress["removed"] += await remove(db, account_id, [ref.id])
            else:
                progress["failed"] += 1
                failed = records(cursor.get("failed_refs", []))
                cursor["failed_refs"] = [*failed, ref.model_dump(mode="json")]
            cursor["pending"] = records(cursor.get("pending", []))[1:]
            cursor.pop("retries", None)
            job.status = "pending"
        elif retries < get_settings().connectors.max_retries:
            cursor["retries"] = retries + 1
            job.status = "waiting"
            job.available_at = datetime.now(UTC) + timedelta(
                seconds=max(error.retry_after, min(300, 2 ** (retries + 1)))
            )
            audit(db, account.owner_id, "sync_retry", account_id, attempt=retries + 1)
        else:
            job.status, account.sync_status = "failed", "failed"
            account.status = "connected_with_warning"
            audit(db, account.owner_id, "sync_failed", account_id, code=error.code)
        job.cursor, job.progress, job.error_message, job.lease_until = cursor, progress, str(error)[:500], None
        if job.status in PENDING:
            account.sync_status = job.status


async def finish(
    identifier: UUID,
    account_id: UUID,
    cursor: dict[str, object],
    progress: dict[str, int],
    mode: str,
    started: datetime,
) -> None:
    config = get_settings().connectors
    async with get_session_factory().begin() as db:
        account = await db.get(ConnectorAccount, account_id, with_for_update=True)
        job = await db.get(ConnectorSyncJob, identifier, with_for_update=True)
        if account is None or job is None or account.status not in ACTIVE or job.status not in PENDING:
            return
        if account.meta.get("pending_events"):
            drain_events(account, cursor)
            job.cursor, job.status, job.lease_until = cursor, "pending", None
            job.available_at = datetime.now(UTC)
            return
        if mode in {"initial", "reconcile"} and not progress["failed"]:
            missing = list(
                await db.scalars(
                    select(ConnectorResource.external_id).where(
                        ConnectorResource.account_id == account_id,
                        ConnectorResource.last_seen_at < started,
                        ConnectorResource.removed_at.is_(None),
                    )
                )
            )
            progress["removed"] += await remove(db, account_id, missing)
        # Change feeds plus verified selected roots permit renewal for unchanged resources.
        groups = strings(cursor.get("inventory_groups", []))
        if groups and not progress["failed"]:
            missing = list(
                await db.scalars(
                    select(ConnectorResource.external_id).where(
                        ConnectorResource.account_id == account_id,
                        ConnectorResource.meta["sync_group"].astext.in_(groups),
                        ConnectorResource.last_seen_at < started,
                        ConnectorResource.removed_at.is_(None),
                    )
                )
            )
            progress["removed"] += await remove(db, account_id, missing)
        # Keep already-removed and deselected resources disabled, and never renew after a failed scan.
        if not progress["failed"]:
            await db.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.connector_account_id == account_id, KnowledgeSource.is_active.is_(True))
                .values(access_expires_at=datetime.now(UTC) + timedelta(seconds=config.acl_ttl_seconds))
            )
        account.sync_cursor = record(cursor.get("checkpoint", {}))
        account.last_sync_at = datetime.now(UTC)
        selection_warning = bool(cursor.get("unavailable_selections"))
        account.sync_status = job.status = (
            "complete_with_warning" if progress["failed"] or selection_warning else "complete"
        )
        account.status = (
            "connected_with_warning"
            if progress["failed"] or selection_warning or cursor.get("notification_warning")
            else "connected"
        )
        account.error_message = (
            "Some resources failed. Retry synchronization to attempt them again." if progress["failed"] else None
        )
        if not account.error_message and cursor.get("notification_warning"):
            account.error_message = (
                "Notifications are unavailable. Periodic polling continues; check provider event setup."
            )
        if selection_warning:
            account.error_message = "Some selected resources are no longer accessible. Update your resource selection."
        account.provider_health, account.authentication_status = "available", "valid"
        account.reconcile_at = datetime.now(UTC) + timedelta(seconds=config.polling_seconds)
        metadata = dict(account.meta)
        metadata["failed_refs"] = cursor.get("failed_refs", [])
        if mode in {"initial", "reconcile"}:
            metadata["last_reconciliation"] = datetime.now(UTC).isoformat()
        account.meta = metadata
        job.progress, job.lease_until = progress, None
        audit(db, account.owner_id, "sync_completed", account_id, **progress)


async def schedule() -> None:
    config = get_settings().connectors
    async with get_session_factory().begin() as db:
        accounts = list(
            await db.scalars(
                select(ConnectorAccount)
                .where(ConnectorAccount.status.in_(ACTIVE), ConnectorAccount.reconcile_at <= datetime.now(UTC))
                .with_for_update(skip_locked=True)
                .limit(10)
            )
        )
        for account in accounts:
            if (
                not configured(cast(Provider, account.provider), config)
                or not Selection.model_validate(account.configuration).resource_ids
            ):
                continue
            last = account.meta.get("last_reconciliation")
            full = (
                not last
                or datetime.fromisoformat(string(last)) + timedelta(seconds=config.reconciliation_seconds)
                <= datetime.now(UTC)
                or account.meta.get("reconcile_requested")
            )
            await enqueue(db, account, "reconcile" if full else "incremental")
            account.reconcile_at = datetime.now(UTC) + timedelta(seconds=config.polling_seconds)
            account.meta = {**account.meta, "reconcile_requested": False}


async def connector_worker() -> None:
    while True:
        try:
            await schedule()
            async with get_session_factory()() as db:
                identifier = await db.scalar(
                    select(ConnectorSyncJob.id)
                    .where(ConnectorSyncJob.status.in_(PENDING), ConnectorSyncJob.available_at <= datetime.now(UTC))
                    .order_by(ConnectorSyncJob.available_at, ConnectorSyncJob.created_at)
                    .limit(1)
                )
            if identifier:
                await process_job(identifier)
        except (SQLAlchemyError, PostgresError, OSError, ValueError, TimeoutError, ConnectorError) as error:
            logger.error("Connector queue unavailable (%s)", type(error).__name__)
        await asyncio.sleep(1)
