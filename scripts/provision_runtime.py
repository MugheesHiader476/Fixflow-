"""Provision a dedicated PostgreSQL runtime login after migrations; preview by default."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets

from asyncpg import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.db.models import Base
from backend.db.session import close_database, get_engine

MANAGED_ROLE = "FixFlow managed runtime v1"


def password_verifier(password: str) -> str:
    """PostgreSQL SCRAM-SHA-256 format; accepted passwords are ASCII (no SASLprep ambiguity)."""
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, 4096)
    stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    encoded = [base64.b64encode(value).decode("ascii") for value in (salt, stored, server)]
    return f"SCRAM-SHA-256$4096:{encoded[0]}${encoded[1]}:{encoded[2]}"


async def provision(connection: AsyncConnection, role: str, password: str, *, apply: bool = False) -> dict[str, object]:
    # Identifiers and password syntax are deliberately narrow; neither is an HTTP input.
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or role.startswith("pg_"):
        raise ValueError("Use a dedicated lowercase runtime role")
    if not re.fullmatch(r"[A-Za-z0-9_=-]{32,128}", password):
        raise ValueError("Use a random 32-128 character URL-safe runtime password")
    owner_password = connection.engine.url.password
    if owner_password and secrets.compare_digest(password.encode(), owner_password.encode()):
        raise ValueError("Runtime and migration passwords must differ")
    current = (await connection.execute(text("SELECT current_user, current_database()"))).one()
    if role == current[0]:
        raise ValueError("Runtime and migration roles must differ")
    existing = (await connection.execute(text(
        "SELECT oid, shobj_description(oid, 'pg_authid') AS managed, "
        "rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls AS elevated "
        "FROM pg_roles WHERE rolname=:role"
    ), {"role": role})).mappings().one_or_none()
    if existing and (
        existing["managed"] != MANAGED_ROLE or existing["elevated"]
        or await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=:oid)"),
                                   {"oid": existing["oid"]})
        or await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_shdepend WHERE refobjid=:oid "
                                          "AND refclassid='pg_authid'::regclass AND deptype='o')"),
                                   {"oid": existing["oid"]})
    ):
        raise ValueError("Refusing to repurpose an unmanaged, elevated, member or object-owning role")
    tables = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    if not (set(Base.metadata.tables) | {"alembic_version"}).issubset(tables):
        raise ValueError("Run all migrations before provisioning runtime access")
    result: dict[str, object] = {"applied": apply, "runtime_role": role, "tables": len(Base.metadata.tables),
                                 "scope": "Dedicated application database; DML only, no schema ownership or migrations"}
    if not apply:
        return result
    quote = connection.dialect.identifier_preparer.quote_identifier
    login, database = quote(role), quote(str(current[1]))
    if not existing:
        await connection.exec_driver_sql(f"CREATE ROLE {login} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                         "NOREPLICATION NOBYPASSRLS NOINHERIT")
        await connection.exec_driver_sql(f"COMMENT ON ROLE {login} IS '{MANAGED_ROLE}'")
    # Send only a salted verifier, never a plaintext password in administrative SQL.
    verifier = password_verifier(password)
    await connection.exec_driver_sql(
        f"ALTER ROLE {login} WITH LOGIN NOINHERIT PASSWORD '{verifier}' CONNECTION LIMIT 40")
    await connection.exec_driver_sql(f"REVOKE CONNECT, TEMPORARY ON DATABASE {database} FROM PUBLIC")
    await connection.exec_driver_sql(f"REVOKE ALL ON DATABASE {database} FROM {login}")
    await connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE {database} TO {login}")
    await connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    await connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA public FROM {login}")
    await connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {login}")
    names = ", ".join(f"public.{quote(name)}" for name in sorted(Base.metadata.tables))
    await connection.exec_driver_sql(f"REVOKE ALL ON TABLE {names}, public.alembic_version FROM {login}")
    await connection.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {names} TO {login}")
    await connection.exec_driver_sql(f"GRANT SELECT ON TABLE public.alembic_version TO {login}")
    await connection.exec_driver_sql(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {login}")
    for setting, value in (("statement_timeout", "60s"), ("lock_timeout", "5s"),
                           ("idle_in_transaction_session_timeout", "300s")):
        await connection.exec_driver_sql(f"ALTER ROLE {login} IN DATABASE {database} SET {setting} = '{value}'")
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply grants atomically to this dedicated database")
    options = parser.parse_args()
    try:
        async with get_engine().begin() as connection:
            result = await provision(connection, os.environ.get("POSTGRES_RUNTIME_USER", "fixflow_runtime"),
                                     os.environ.get("POSTGRES_RUNTIME_PASSWORD", ""), apply=options.apply)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, SQLAlchemyError, PostgresError, OSError):
        print(json.dumps({"applied": False,
                          "error": "Runtime provisioning failed; review private configuration/grants"}))
        return 1
    finally:
        await close_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
