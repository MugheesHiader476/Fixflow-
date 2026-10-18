"""Actual PostgreSQL privileges, data preservation and administrative input bounds."""

from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError

from backend.db.models import SavedSolution
from backend.db.session import get_engine
from scripts.provision_runtime import provision

pytestmark = pytest.mark.anyio
PASSWORD = "test-only-runtime-password-" + "x" * 32


async def test_runtime_grants_preserve_data_and_deny_ddl(database: None) -> None:
    role = "qa_runtime_" + uuid4().hex
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(SavedSolution).values(
                id=uuid4(), owner_id="user_qa_runtime", payload={"problem": "Preserved finding"}))
            preview = await provision(connection, role, PASSWORD)
            assert preview["applied"] is False
            assert not await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"),
                                               {"role": role})
            assert (await provision(connection, role, PASSWORD, apply=True))["applied"] is True
            await provision(connection, role, PASSWORD, apply=True)  # Repeat safely after every migration.
        async with engine.begin() as connection:
            verifier = await connection.scalar(text("SELECT rolpassword FROM pg_authid WHERE rolname=:role"),
                                               {"role": role})
            assert str(verifier).startswith("SCRAM-SHA-256$") and PASSWORD not in str(verifier)
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert await connection.scalar(text("SELECT current_user")) == role
            saved = await connection.scalar(text("SELECT payload->>'problem' FROM saved_solutions"))
            assert saved == "Preserved finding"
            await connection.execute(text("UPDATE saved_solutions SET payload='{}'::jsonb WHERE owner_id=:owner"),
                                     {"owner": "user_qa_runtime"})
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0010"
            assert await connection.scalar(text("SELECT pg_try_advisory_xact_lock(987654321)"))
            assert not await connection.scalar(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole "
                                                   "OR rolreplication OR rolbypassrls FROM pg_roles "
                                                   "WHERE rolname=current_user"))
        for sql in ("CREATE TABLE public.qa_forbidden (id integer)", "CREATE TEMP TABLE qa_forbidden (id integer)",
                    "DROP TABLE saved_solutions", "TRUNCATE saved_solutions", "DELETE FROM alembic_version"):
            with pytest.raises(DBAPIError):
                async with engine.begin() as connection:
                    await connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                    await connection.exec_driver_sql(sql)
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT payload FROM saved_solutions")) == {}
    finally:
        async with engine.begin() as connection:
            exists = await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"),
                                             {"role": role})
            if exists:
                await connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
                await connection.exec_driver_sql(f'DROP ROLE "{role}"')


async def test_provisioning_rejects_existing_privileged_roles_and_untrusted_syntax(database: None) -> None:
    async with get_engine().begin() as connection:
        current = str(await connection.scalar(text("SELECT current_user")))
        for role in (current, "pg_monitor", 'runtime"; DROP DATABASE postgres; --', "UPPERCASE"):
            with pytest.raises(ValueError):
                await provision(connection, role, PASSWORD, apply=True)
        with pytest.raises(ValueError):
            await provision(connection, "qa_runtime", "too-short", apply=True)


async def test_provisioning_does_not_take_over_an_existing_unmanaged_login(database: None) -> None:
    role = "qa_unmanaged_" + uuid4().hex
    async with get_engine().begin() as connection:
        await connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        try:
            with pytest.raises(ValueError, match="unmanaged"):
                await provision(connection, role, PASSWORD, apply=True)
            assert not await connection.scalar(text("SELECT rolcanlogin FROM pg_roles WHERE rolname=:role"),
                                               {"role": role})
        finally:
            await connection.exec_driver_sql(f'DROP ROLE "{role}"')
