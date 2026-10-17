import json
import os
import sys
from pathlib import Path

import pytest

from scripts.evaluate import ROOT, main, run_gate, save_report


def test_failed_command_is_not_reported_as_pass(tmp_path: Path) -> None:
    result = run_gate("intentional-failure", [sys.executable, "-c", "raise SystemExit(7)"], tmp_path, dict(os.environ))
    assert result["status"] == "FAIL" and result["exit_code"] == 7
    save_report(tmp_path, [result])
    assert json.loads((tmp_path / "summary.json").read_text())["passed"] is False


def test_evaluation_rejects_application_database_before_running_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["evaluate", "--output", str(ROOT / ".local/evaluation-guard")])
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://example@localhost/application")
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 2
