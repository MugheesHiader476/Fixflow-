"""Reproducible frontend, backend, security and Playwright evaluation with saved evidence."""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import signal
import socket
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import TypedDict

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


class Result(TypedDict):
    name: str
    status: str
    exit_code: int
    seconds: float
    command: list[str]
    log: str


def run_gate(
    name: str,
    command: list[str],
    output: Path,
    environment: dict[str, str],
    *,
    warning: bool = False,
    timeout: int = 600,
) -> Result:
    print(f"START {name}", flush=True)
    started = time.monotonic()
    path = output / f"{name}.log"
    with path.open("w") as log:
        path.chmod(0o600)
        # Commands are fixed by this CLI, never accepted from an HTTP request.
        child = subprocess.Popen(  # nosec B603
            command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            code = 124
            log.write("\nEvaluation command exceeded its deadline.\n")
    status = "PASS" if code == 0 else "WARN" if warning else "FAIL"
    print(f"{status} {name} ({time.monotonic() - started:.1f}s)", flush=True)
    return {
        "name": name,
        "status": status,
        "exit_code": code,
        "seconds": round(time.monotonic() - started, 2),
        "command": command,
        "log": path.name,
    }


def save_report(output: Path, results: list[Result]) -> None:
    report = {
        "boundary": "Real application and disposable PostgreSQL; simulated external boundaries labelled",
        "results": results,
        "passed": not any(row["status"] == "FAIL" for row in results),
        "warnings": sum(row["status"] == "WARN" for row in results),
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    rows = "".join(
        f"<tr><td>{html.escape(row['name'])}</td><td>{row['status']}</td><td>{row['seconds']}</td>"
        f'<td><a href="{row["log"]}">Log</a></td></tr>'
        for row in results
    )
    (output / "report.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>FixFlow evaluation evidence</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;"
        "padding:20px;color:#232722;background:#f8f9f6}table{border-collapse:collapse;width:100%}"
        "td,th{padding:12px;text-align:left;border-bottom:1px solid #ccd2c8}a{color:#254e78}</style>"
        "<h1>FixFlow evaluation evidence</h1><p>Each row records a command, its exit status, duration and log.</p>"
        "<p>External identity/provider simulation is documented in the browser reports. A warning is not a pass.</p>"
        "<table><thead><tr><th>Gate</th><th>Result</th><th>Seconds</th><th>Evidence</th></tr></thead>"
        f"<tbody>{rows}</tbody></table><p><a href='summary.json'>Machine-readable summary</a> · "
        "<a href='frontend-junit.xml'>Frontend JUnit</a> · <a href='backend-junit.xml'>Backend JUnit</a> · "
        "<a href='fullstack-results.json'>Real stack browser results</a> · "
        "<a href='canonical-sql-proof.json'>Canonical SQL proof</a></p></html>"
    )


def browser_gates(output: Path, environment: dict[str, str]) -> list[Result]:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 4173)) == 0:
            raise ValueError("Port 4173 is occupied; stop only your existing test harness before evaluation")
    node = shutil.which("node")
    if node is None:
        raise ValueError("Node.js is required")
    with (output / "vite.log").open("w") as log:
        # Fixed local test-harness entry point; no caller-supplied command.
        service = subprocess.Popen(  # nosec B603
            [node, "node_modules/vite/bin/vite.js", "--config", "tests/browser/vite.config.mts"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            for _ in range(100):
                if service.poll() is not None:
                    raise ValueError("Browser component harness exited before startup")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", 4173)) == 0:
                        break
                time.sleep(0.2)
            else:
                raise ValueError("Browser component harness did not become ready")
            results = [
                run_gate(name, ["node", f"tests/browser/{script}"], output, environment, timeout=180)
                for name, script in (
                    ("browser-workspace", "verify.mjs"),
                    ("browser-connectors", "verify-connectors.mjs"),
                    ("browser-visuals", "verify-visuals.mjs"),
                    ("browser-evidence", "verify-evidence.mjs"),
                    ("browser-agent", "verify-agent.mjs"),
                )
            ]
            shutil.copytree(ROOT / ".local/browser-evidence", output / "component-browser", dirs_exist_ok=True)
            return results
        finally:
            if service.poll() is None:
                os.killpg(service.pid, signal.SIGTERM)
                try:
                    service.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(service.pid, signal.SIGKILL)
                    service.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/evaluation")
    options = parser.parse_args()
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / ".local"):
        parser.error("Evidence output must be inside this repository's ignored .local directory")
    url = os.environ.get("TEST_DATABASE_URL", "")
    if (
        not url
        or make_url(url).get_backend_name() != "postgresql"
        or not (make_url(url).database or "").endswith("_test")
    ):
        parser.error("Provide an explicit disposable PostgreSQL TEST_DATABASE_URL ending in _test")
    if not os.environ.get("FIXFLOW_BROWSER_TOOLS"):
        parser.error("Provide FIXFLOW_BROWSER_TOOLS for the external Playwright installation")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {
        **os.environ,
        "DATABASE_URL": url,
        "FIXFLOW_EVALUATION_DIR": str(output),
        "FIXFLOW_TEST_PYTHON": sys.executable,
    }
    # Never permit the iteration-only build shortcut in the complete evaluation.
    environment.pop("FIXFLOW_REUSE_EVALUATION_BUILD", None)
    python = sys.executable
    steps: list[tuple[str, list[str]]] = [
        ("frontend-lint", ["npm", "run", "lint"]),
        ("frontend-typecheck", ["npm", "run", "typecheck"]),
        (
            "frontend-coverage",
            [
                "npm",
                "run",
                "test:coverage",
                "--",
                "--maxWorkers=1",
                "--reporter=default",
                "--reporter=junit",
                f"--outputFile.junit={output / 'frontend-junit.xml'}",
            ],
        ),
        ("python-ruff", [python, "-m", "ruff", "check", "backend", "scripts"]),
        ("python-mypy", [python, "-m", "mypy", "backend", "scripts"]),
        (
            "python-bandit",
            [python, "-m", "bandit", "-r", "backend", "scripts", "-x", "backend/tests,scripts/tests", "-q"],
        ),
        ("backend-coverage", [python, "-m", "pytest", f"--junitxml={output / 'backend-junit.xml'}"]),
        ("migration-schema-check", [python, "-m", "alembic", "check"]),
        ("python-dependency-audit", [python, "-m", "pip_audit", "-r", "requirements-dev.lock.txt", "--format=json"]),
        ("npm-production-audit", ["npm", "audit", "--omit=dev", "--json"]),
        ("production-build", ["npm", "run", "build"]),
        (
            "client-secret-scan",
            [python, "scripts/audit_client.py", "--output", str(output / "client-secret-scan.json")],
        ),
    ]
    results: list[Result] = []
    for name, command in steps:
        results.append(run_gate(name, command, output, environment))
        save_report(output, results)
    results.append(run_gate("npm-development-audit", ["npm", "audit", "--json"], output, environment, warning=True))
    save_report(output, results)
    try:
        results.extend(browser_gates(output, environment))
        results.append(
            run_gate("real-fullstack-playwright", ["node", "tests/browser/run-fullstack.mjs"], output, environment)
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        (output / "browser-infrastructure.log").write_text(f"Browser infrastructure failed: {type(error).__name__}\n")
        results.append(
            {
                "name": "browser-infrastructure",
                "status": "FAIL",
                "exit_code": 1,
                "seconds": 0,
                "command": [],
                "log": "browser-infrastructure.log",
            }
        )
    finally:
        save_report(output, results)
    print(f"Evidence: {output / 'report.html'}")
    raise SystemExit(1 if any(row["status"] == "FAIL" for row in results) else 0)


if __name__ == "__main__":
    main()
