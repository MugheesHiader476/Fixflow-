"""Private version-bound download tickets; no provider URL or credential in the browser."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.db.models import AgentFile
from backend.db.session import get_session_factory
from backend.schemas.agent import FileView, PrepareFile
from backend.schemas.pipeline import digest
from backend.security.policy import PolicyViolation, redact
from backend.security.runtime import configured_secrets
from backend.services.workspace_tools import Context, authorize, connection, ensure_mutable, lock_authority


def view(file: AgentFile) -> FileView:
    try:
        name = str(redact(file.filename, configured_secrets()))
    except PolicyViolation:
        name = "Private file"
    return FileView(
        id=file.id,
        filename=name,
        mime_type=file.mime_type,
        download_path=f"/api/agent/files/{file.id}/download",
    )


async def prepare(ctx: Context, args: PrepareFile) -> dict[str, object]:
    await ensure_mutable(ctx)
    workspace, _ = await connection(ctx, args.account_id)
    filename, mime, reference = await workspace.file_info(args)
    payload = args.model_dump(mode="json")
    fingerprint = digest([payload, reference])
    async with get_session_factory().begin() as db:
        await lock_authority(db, ctx, args.account_id)
        await db.execute(
            insert(AgentFile)
            .values(
                id=uuid4(),
                run_id=ctx.run_id,
                owner_id=ctx.owner,
                account_id=args.account_id,
                request=payload,
                request_hash=fingerprint,
                reference=reference,
                filename=filename,
                mime_type=mime,
            )
            .on_conflict_do_nothing()
        )
        file = await db.scalar(
            select(AgentFile).where(
                AgentFile.run_id == ctx.run_id, AgentFile.owner_id == ctx.owner, AgentFile.request_hash == fingerprint
            )
        )
        if file is None:
            raise HTTPException(409, "The file ticket could not be persisted")
        return view(file).model_dump(mode="json")


async def download(ctx: Context, identifier: UUID) -> tuple[bytes, str, str]:
    await authorize(ctx)
    async with get_session_factory()() as db:
        file = await db.scalar(
            select(AgentFile).where(
                AgentFile.id == identifier, AgentFile.run_id == ctx.run_id, AgentFile.owner_id == ctx.owner
            )
        )
        if file is None:
            raise HTTPException(404, "File not found")
    workspace, _ = await connection(ctx, file.account_id)
    data = await workspace.file_bytes(PrepareFile.model_validate(file.request), file.reference)
    await authorize(ctx)
    await connection(ctx, file.account_id)
    return data, view(file).filename, file.mime_type
