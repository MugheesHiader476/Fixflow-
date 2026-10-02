"""Provider-independent envelope registration and permission-safe source lifecycle."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.connectors.core import ConnectorError
from backend.db.models import ConnectorAccount, ConnectorResource, KnowledgeSource
from backend.schemas.connectors import RawSourceEnvelope, Resource
from backend.services.uploads import discard_upload, save_upload


def asset_path(reference: str) -> Path:
    if not reference.startswith("upload:"):
        raise ConnectorError("invalid_asset", "Unsupported private asset reference")
    relative = Path(reference.removeprefix("upload:"))
    root = get_settings().upload_dir.resolve()
    path = root / relative
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or ".." in relative.parts
        or path.is_symlink()
        or not path.resolve().is_relative_to(root)
    ):
        raise ConnectorError("invalid_asset", "Invalid private asset reference")
    return path


async def reusable_file(db: AsyncSession, account: ConnectorAccount, resource: Resource) -> RawSourceEnvelope | None:
    """Reuse verified file bytes when provider listing gives the same immutable version."""
    if resource.kind not in {"source_file", "drive_file"} or not resource.version:
        return None
    if resource.metadata.get("can_download") is False:
        raise ConnectorError("inaccessible", "Drive file owner disabled downloads")
    row = await db.scalar(
        select(ConnectorResource).where(
            ConnectorResource.account_id == account.id,
            ConnectorResource.external_id == resource.id,
            ConnectorResource.external_version == resource.version,
        )
    )
    source = await db.get(KnowledgeSource, row.source_id) if row and row.source_id else None
    if source is None or source.status == "failed" or not source.path:
        return None
    context = source.ingestion_metadata.get("source_envelope")
    if not isinstance(context, dict):
        return None
    path = Path(source.path)
    if (
        path.is_symlink()
        or not path.is_file()
        or not path.resolve().is_relative_to(get_settings().upload_dir.resolve())
    ):
        return None
    permissions = dict(context["permissions"]) if isinstance(context.get("permissions"), dict) else {}
    permissions["verified_at"] = datetime.now(UTC).isoformat()
    if "provider_permissions" in resource.metadata:
        permissions["provider_permissions"] = resource.metadata["provider_permissions"]
    metadata = {**(context["metadata"] if isinstance(context.get("metadata"), dict) else {}), **resource.metadata}
    return RawSourceEnvelope.model_validate(
        {
            **context,
            "metadata": metadata,
            "permissions": permissions,
            "fetched_at": datetime.now(UTC),
            "binary_asset_reference": "upload:" + str(path.relative_to(get_settings().upload_dir)),
        }
    )


async def register(
    db: AsyncSession, account: ConnectorAccount, envelope: RawSourceEnvelope, assets: list[Path] | None = None
) -> bool:
    if (
        envelope.connector_account_id != account.id
        or envelope.permissions.application_owner != account.owner_id
        or envelope.provider != account.provider
    ):
        raise ConnectorError("invalid_envelope", "Envelope authorization does not match the connection")
    key = int.from_bytes(envelope.source_id.bytes[:8], "big", signed=True)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise ConnectorError("processing", "Source is currently processing; synchronization will resume", 5)
    path = asset_path(envelope.binary_asset_reference) if envelope.binary_asset_reference else None
    resource = await db.scalar(
        select(ConnectorResource).where(
            ConnectorResource.account_id == account.id, ConnectorResource.external_id == envelope.external_id
        )
    )
    source = await db.get(KnowledgeSource, envelope.source_id)
    if source and source.path and assets is not None:
        assets.append(Path(source.path))
    context = envelope.context()
    changed = source is None or source.file_hash != envelope.content_hash or source.status == "failed"
    # Metadata/permission updates enter the same provider-independent pipeline; bytes are reused when possible.
    old_context = source.ingestion_metadata.get("source_envelope") if source else None
    if isinstance(old_context, dict):
        ignored = {"fetched_at", "permissions"}
        changed = changed or {k: v for k, v in old_context.items() if k not in ignored} != {
            k: v for k, v in context.items() if k not in ignored
        }
    if source and source.owner_id != account.owner_id:
        raise ConnectorError("invalid_envelope", "Source ownership mismatch")
    if changed:
        if path is None:
            path, digest = await save_upload(envelope.filename, None, envelope.content)
            if assets is not None:
                assets.append(path)
        else:
            digest = await asyncio.to_thread(file_hash, path)
        if digest != envelope.content_hash:
            if path:
                discard_upload(path)
            raise ConnectorError("invalid_asset", "Source asset integrity check failed")
        if source is None:
            identity = hashlib.sha256(
                f"{envelope.provider}\0{account.external_account_id}\0{envelope.external_id}".encode()
            ).hexdigest()
            source = KnowledgeSource(
                id=envelope.source_id,
                owner_id=account.owner_id,
                name=envelope.filename,
                file_hash=digest,
                source_type="github" if envelope.provider == "github" else "upload",
                connector_account_id=account.id,
                external_identity=identity,
            )
            db.add(source)
        source.path, source.name, source.file_hash = str(path), envelope.filename, digest
        source.status, source.error_message = "uploaded", None
        source.embedding_status = "not_configured"
        source.ingestion_metadata = {"source_envelope": context}
        await db.flush()
    elif path and source and str(path) != source.path:
        discard_upload(path)
    if source is None:
        raise ConnectorError("invalid_envelope", "Source registration could not be completed")
    source.is_active, source.access_expires_at = (
        True,
        datetime.now(UTC) + timedelta(seconds=get_settings().connectors.acl_ttl_seconds),
    )
    if not changed:
        # Source rows enforce retrieval permission; artifacts carry the same evidence.
        source.ingestion_metadata = {**source.ingestion_metadata, "source_envelope": context}
        await refresh_artifact_permissions(db, source.id, context)
    if resource is None:
        resource = ConnectorResource(account_id=account.id, external_id=envelope.external_id)
        db.add(resource)
    resource.source_id, resource.external_version, resource.content_hash = (
        source.id,
        envelope.external_version,
        envelope.content_hash,
    )
    resource.external_parent_id, resource.permissions, resource.meta = (
        envelope.external_parent_id,
        envelope.permissions.model_dump(mode="json"),
        envelope.metadata,
    )
    resource.last_seen_at, resource.removed_at = datetime.now(UTC), None
    return changed


async def discard_unreferenced(paths: list[Path]) -> None:
    if not paths:
        return
    from backend.db.session import get_session_factory  # noqa: PLC0415

    async with get_session_factory()() as db:
        retained = set(
            await db.scalars(select(KnowledgeSource.path).where(KnowledgeSource.path.in_([str(p) for p in paths])))
        )
    root = get_settings().upload_dir.resolve()
    for path in set(paths):
        if str(path) not in retained and not path.is_symlink() and path.resolve().is_relative_to(root):
            discard_upload(path)


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


async def refresh_artifact_permissions(db: AsyncSession, source_id: UUID, context: dict[str, object]) -> None:
    from backend.db.models import IngestionArtifact  # noqa: PLC0415
    from backend.processing.pipeline.context import apply_context  # noqa: PLC0415
    from backend.repositories.pipeline import persist_result  # noqa: PLC0415
    from backend.schemas.pipeline import PipelineResult  # noqa: PLC0415

    artifact = await db.get(IngestionArtifact, source_id)
    if artifact:
        result = PipelineResult.model_validate(artifact.result)
        apply_context(result, context)
        await persist_result(db, source_id, result)


async def remove(db: AsyncSession, account_id: object, identifiers: list[str]) -> int:
    """Remove a resource and its descendants without leaking stale chunks into retrieval."""
    resources = list(await db.scalars(select(ConnectorResource).where(ConnectorResource.account_id == account_id)))
    removed = set(identifiers)
    while True:
        children = {r.external_id for r in resources if r.external_parent_id in removed} - removed
        if not children:
            break
        removed.update(children)
    ids = []
    for resource in resources:
        if resource.external_id in removed and resource.removed_at is None:
            resource.removed_at = datetime.now(UTC)
            if resource.source_id:
                ids.append(resource.source_id)
    if ids:
        await db.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.id.in_(ids))
            .values(is_active=False, access_expires_at=datetime.now(UTC))
        )
    return len(ids)
