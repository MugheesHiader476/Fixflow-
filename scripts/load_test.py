"""Bounded, local two-replica QA against an explicitly disposable PostgreSQL database."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import signal
import socket
import subprocess  # nosec B404
import sys
import time
from collections import Counter
from pathlib import Path
from typing import IO, NotRequired, TypedDict
from uuid import uuid4

import httpx
from asyncpg import PostgresError
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import ROOT
from backend.connectors.config import ConnectorConfig
from backend.db.models import Base
from backend.schemas.agent import GrokConfig
from backend.schemas.evidence import EvidenceConfig, GeminiConfig
from backend.security.config import SecurityConfig
from scripts.provision_runtime import provision


class Workload(TypedDict):
    requests: int
    concurrency: int
    statuses: dict[int, int]
    seconds: float
    requests_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mode: NotRequired[str]


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validated_database(value: str) -> str:
    url = make_url(value)
    if (url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test")
            or url.host not in {"127.0.0.1", "localhost", "::1"} or url.query):
        raise ValueError("An explicit loopback PostgreSQL database ending in _test is required")
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


async def workload(client: httpx.AsyncClient, base: str, owner: str, count: int, concurrency: int) -> Workload:
    semaphore = asyncio.Semaphore(concurrency)
    durations: list[float] = []
    statuses: Counter[int] = Counter()
    paths = ("/api/sources", "/api/saved", "/api/sessions")

    async def request(index: int) -> None:
        async with semaphore:
            started = time.perf_counter()
            response = await client.get(base + paths[index % len(paths)], headers={"X-FixFlow-User-Id": owner})
            durations.append((time.perf_counter() - started) * 1000)
            statuses[response.status_code] += 1
            if response.status_code == 429:
                require(int(response.headers["retry-after"]) > 0, "Rate response omitted a valid retry delay")
            if response.status_code == 200:
                require(response.json() == [], "Cross-owner data appeared in an empty test account")

    started = time.perf_counter()
    await asyncio.gather(*(request(index) for index in range(count)))
    elapsed = time.perf_counter() - started
    ordered = sorted(durations)
    return {"requests": count, "concurrency": concurrency, "statuses": dict(statuses),
            "seconds": round(elapsed, 3), "requests_per_second": round(count / elapsed, 2),
            "p50_ms": round(ordered[int((count - 1) * .50)], 2),
            "p95_ms": round(ordered[int((count - 1) * .95)], 2),
            "p99_ms": round(ordered[int((count - 1) * .99)], 2)}


async def evaluate(nginx: Path, output: Path) -> dict[str, object]:
    database = validated_database(os.environ.get("TEST_DATABASE_URL", ""))
    for port in (8021, 8022, 8023):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(f"QA port {port} is occupied")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CONNECTORS", "GROK", "EVIDENCE", "GEMINI", "SECURITY"))}
    token, role, password = secrets.token_urlsafe(40), "qa_load_" + uuid4().hex, secrets.token_urlsafe(40)
    environment.update({"DATABASE_URL": database, "FIXFLOW_API_TOKEN": token,
                        "FIXFLOW_DATA_DIR": str(output / "uploads"),
                        "CONNECTORS": ConnectorConfig().model_dump_json(), "GROK": GrokConfig().model_dump_json(),
                        "EVIDENCE": EvidenceConfig().model_dump_json(), "GEMINI": GeminiConfig().model_dump_json(),
                        "SECURITY": SecurityConfig().model_dump_json(),
                        "DB_POOL_SIZE": "5", "DB_MAX_OVERFLOW": "5", "PYTHONUNBUFFERED": "1"})
    # This script never resets a database, even one named _test.
    with (output / "migrations.log").open("w") as log:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
                       env=environment, stdout=log, stderr=log, check=True, timeout=60)  # nosec B603
    admin = create_async_engine(database, hide_parameters=True)
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[IO[bytes]] = []
    report: dict[str, object] = {"boundary": "Local HTTP, real Nginx and two FastAPI workers, real PostgreSQL; "
                                           "no live Clerk/provider/model; bounded concurrency, not production capacity",
                               "passed": False, "workloads": []}
    created = False
    try:
        async with admin.begin() as connection:
            for table in Base.metadata.sorted_tables:
                if await connection.scalar(select(func.count()).select_from(table)):
                    raise ValueError("Use a new empty disposable database for load evaluation")
            await provision(connection, role, password, apply=True)
            created = True
        runtime_url = make_url(database).set(username=role, password=password).render_as_string(hide_password=False)
        environment["DATABASE_URL"] = runtime_url
        runtime = create_async_engine(runtime_url, hide_parameters=True)
        try:
            async with runtime.connect() as connection:
                require(await connection.scalar(text("SELECT current_user")) == role, "Runtime login was not used")
                privileges = (await connection.execute(text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname=current_user"))).mappings().one()
                require(not any(privileges.values()), "Runtime login has administrative privileges")
                report["database_role"] = dict(privileges)
                report["database_timeouts"] = {key: await connection.scalar(text(f"SHOW {key}")) for key in
                                               ("statement_timeout", "lock_timeout",
                                                "idle_in_transaction_session_timeout")}
        finally:
            await runtime.dispose()

        def start(name: str, command: list[str]) -> subprocess.Popen[bytes]:
            log = (output / f"{name}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(command, cwd=ROOT, env=environment,  # nosec B603
                                       stdout=log, stderr=log, start_new_session=True)
            processes.append(process)
            return process

        for port in (8021, 8022):
            start(f"backend-{port}", [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                                     "--port", str(port), "--no-proxy-headers"])
        config = (ROOT / "deploy/nginx.conf").read_text().replace(
            "resolver 127.0.0.11 valid=10s ipv6=off;", "").replace(
            "server backend:8000 resolve max_fails=1 fail_timeout=5s;",
            "server 127.0.0.1:8021 max_fails=1 fail_timeout=5s;\n"
            "        server 127.0.0.1:8022 max_fails=1 fail_timeout=5s;")
        config = config.replace("listen 8000;", "listen 8023;")
        for directive, filename in (("pid", "nginx.pid"), ("client_body_temp_path", "client_body"),
                                    ("proxy_temp_path", "proxy")):
            replacement = f"{directive} {output / filename};".replace("\\", "\\\\")
            config = re.sub(rf"{directive} [^;]+;", replacement, config)
        config_path = output / "nginx.conf"
        config_path.write_text(config)
        start("nginx", [str(nginx), "-p", str(output), "-c", str(config_path), "-g", "daemon off;"])
        async with httpx.AsyncClient(timeout=20, headers={"Authorization": "Bearer " + token}) as client:
            for port in (8021, 8022, 8023):
                for _ in range(100):
                    require(all(process.poll() is None for process in processes), "QA service exited; inspect logs")
                    try:
                        response = await client.get(f"http://127.0.0.1:{port}/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(.2)
                else:
                    raise ValueError("QA service did not become ready")
            results: list[Workload] = []
            for name, port in (("single", 8021), ("balanced", 8023)):
                for concurrency in (1, 8, 24):
                    result = await workload(client, f"http://127.0.0.1:{port}", f"user_qa_{name}_{concurrency}",
                                            120, concurrency)
                    result["mode"] = name
                    results.append(result)
                    require(result["statuses"] == {200: 120}, "Unexpected workload failure")
                    print(json.dumps(result), flush=True)
            report["workloads"] = results
            balanced = "http://127.0.0.1:8023"
            quota = await workload(client, balanced, "user_qa_shared_quota", 270, 24)
            report["shared_quota"] = quota
            require(quota["statuses"] == {200: 240, 429: 30}, "Replicas did not share the durable owner quota")
            failures = [await client.get(balanced + "/api/sources", headers={"Authorization": "Bearer forged"})
                        for _ in range(25)]
            report["forged_auth"] = dict(Counter(response.status_code for response in failures))
            require(report["forged_auth"] == {401: 10, 429: 15}, "Gateway failure limits were not enforced")
            valid = await client.get(balanced + "/api/sources", headers={"X-FixFlow-User-Id": "user_qa_valid"})
            require(valid.status_code == 200, "Anonymous failures locked out a valid owner")

            async def upload() -> str:
                response = await client.post(balanced + "/api/documents",
                    headers={"X-FixFlow-User-Id": "user_qa_upload"},
                    files={"file": ("load.md", b"# QA load runbook\n\nPreserve one canonical document.")})
                require(response.status_code == 202, "Concurrent upload was not accepted")
                return str(response.json()["id"])

            identities = await asyncio.gather(*(upload() for _ in range(12)))
            require(len(set(identities)) == 1, "Duplicate concurrent uploads created different source identities")
            for _ in range(100):
                source = await client.get(balanced + "/api/sources/" + identities[0],
                                          headers={"X-FixFlow-User-Id": "user_qa_upload"})
                if source.json().get("status") == "parsed":
                    break
                await asyncio.sleep(.3)
            require(source.json()["status"] == "parsed" and source.json()["document_count"] == 1,
                    "Workers failed to preserve one canonical document")
            report["upload_flow"] = {"accepted": 12, "source_id": identities[0], "status": "parsed", "documents": 1}
            foreign = await client.get(balanced + "/api/sources/" + identities[0],
                                       headers={"X-FixFlow-User-Id": "user_qa_foreign"})
            require(foreign.status_code == 404, "Another owner could read the uploaded source")
            # Wait for log writes before proving both upstreams were used.
            upstreams = Counter(line.split()[3] for line in (output / "nginx.log").read_text().splitlines()
                                if line.startswith("GET /api/") and len(line.split()) == 5)
            report["upstream_requests"] = dict(upstreams)
            require(upstreams["127.0.0.1:8021"] > 0 and upstreams["127.0.0.1:8022"] > 0,
                    "Requests did not reach both replicas")
            os.killpg(processes[0].pid, signal.SIGTERM)
            await asyncio.to_thread(processes[0].wait, 10)
            failover = await workload(client, balanced, "user_qa_failover", 40, 1)
            report["failover"] = failover
            require(failover["statuses"].get(200, 0) >= 39, "Surviving replica did not serve failover traffic")
            require(set(failover["statuses"]).issubset({200, 502}), "Unexpected failover failure")
            for forbidden in (token, password, "Bearer forged"):
                require(forbidden not in (output / "nginx.log").read_text(), "Balancer logged a credential")
            report["passed"] = True
        return report
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(process.wait, 10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.to_thread(process.wait, 5)
        for service_log in logs:
            service_log.close()
        if created:
            async with admin.begin() as connection:
                await connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
                await connection.exec_driver_sql(f'DROP ROLE "{role}"')
        await admin.dispose()
        (output / "load-results.json").write_text(json.dumps(report, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", type=Path, required=True, help="Existing local Nginx >=1.28 binary")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/load-test")
    options = parser.parse_args()
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / ".local") or not options.nginx.resolve().is_file():
        parser.error("Use a local Nginx binary and an output directory inside .local")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        asyncio.run(evaluate(options.nginx.resolve(), output))
    except (ValueError, AssertionError, OSError, httpx.HTTPError, subprocess.SubprocessError,
            SQLAlchemyError, PostgresError, TimeoutError):
        # URLs and driver exceptions may contain private credentials; preserve only safe failure text.
        print("FAIL load evaluation; inspect private service logs and load-results.json", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS bounded local load evaluation: {output / 'load-results.json'}")


if __name__ == "__main__":
    main()
