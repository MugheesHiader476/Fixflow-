import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import get_settings
from backend.db.proofos import ProofProject


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=ProofProject.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def migrate_online() -> None:
    engine = create_async_engine(get_settings().sqlalchemy_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=get_settings().sqlalchemy_url(), target_metadata=ProofProject.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(migrate_online())
